#!/usr/bin/python
import os
import sys
import csv
import json
import uuid
import time
import zipfile
import sqlite3
from datetime import date
from optparse import OptionParser
from pprint import pprint
from numpy import mean

# Using variant results file alleles.txt, this script update VariantBase insertings new variants and variantMetrics
# USAGE : python insert_db_variants.py --analysis analysisID --variants /.../alleles.xls

def dict_factory(cursor, row):
    d = {}
    for idx, col in enumerate(cursor.description):
        d[col[0]] = row[idx]
    return d

### GATHERING PARAMETERS ############################################################

parser = OptionParser()
parser.add_option('-a', '--analysis',	help="DB analysisID", dest='analysis')
parser.add_option('-v', '--vcf',		help="VCF file (one or more : --vcf 1.vcf --vcf 2.vcf)", action="append", dest='vcfs')
parser.add_option('-z', '--abl1',		help="abl1 conversion", dest='abl1', default='no')
(options, args) = parser.parse_args()

pipeline_folder = os.environ['NGS_PIPELINE_BX_DIR']
with open('%s/global_parameters.json' % pipeline_folder, 'r') as g:
	global_param = json.loads(g.read().replace('$NGS_PIPELINE_BX_DIR',os.environ['NGS_PIPELINE_BX_DIR']))
	
db_path = global_param['VariantBase']
db_con = sqlite3.connect(db_path)
db_con.row_factory = dict_factory
db_cur = db_con.cursor()

analysis_id = options.analysis
genome_build = 'hg19'
vc_tool = '?'

db_cur.execute("SELECT * FROM Gene")
db_genes = db_cur.fetchall()
gene_data = {}
for db_gene in db_genes:
	gene_data[db_gene['geneID']] = {
		'chromosome': db_gene['chromosome'],
		'transcriptionStart': db_gene['transcriptionStart'],
		'transcriptionStop': db_gene['transcriptionStop']
	}

abl1_c2g = {}
if options.abl1 == 'yes':
	abl1_cdna2genomic = global_param['abl1_cdna2genomic']
	abl1_cdna2genomic_file = open(abl1_cdna2genomic,'r')
	abl1_cdna2genomic_reader = csv.reader(abl1_cdna2genomic_file,delimiter='\t')
	abl1_cdna2genomic_reader.next() # header
	for line in abl1_cdna2genomic_reader:
		abl1_c2g[int(line[2])] = int(line[3])
		
mutect2_nocall = ['base_qual','low_allele_frac','map_qual','slippage','strand_bias','strict_strand','weak_evidence']

#   __        __   __          __       __   ___  __            ___  __  
#  |__)  /\  |__) /__` | |\ | / _`     |__) |__  /__` |  | |     |  /__` 
#  |    /~~\ |  \ .__/ | | \| \__>     |  \ |___ .__/ \__/ |___  |  .__/ 

variants = {}

for vcf_path in options.vcfs:
	vcf = open(vcf_path,'r')
	for line in vcf:
		if line.startswith('#'):
			continue
		line = line.replace('\n','').split('\t')
		chromosome = line[0]
		position = int(line[1])
		oref = line[3]
		alts = line[4].split(',')
		if 'tvc_de_novo' in vcf_path:
			vc_name = 'tvc(de_novo)'
		elif 'tvc_only_hotspot' in vcf_path:
			vc_name = 'tvc(hotspot)'

		formats = line[8].split(':')
		if line[9].split(':')[formats.index('GT')] not in ['0/0','./.']:
			alleles_called = set(line[9].split(':')[formats.index('GT')].split('/')) # sometimes weird GT = '4/8' for eg. means 4th and 8th alternative alleles are called
			if 'FDP' in formats:
				pos_cov = int(line[9].split(':')[formats.index('FDP')])
			else:
				pos_cov = int(line[9].split(':')[formats.index('DP')])
			if 'FAO' in formats:
				var_covs = line[9].split(':')[formats.index('FAO')].split(',')
			else:
				var_covs = line[9].split(':')[formats.index('AO')].split(',')
		else:
			continue
			
		for a in range(len(alts)): # si multiallelic
			if str(a+1) not in alleles_called:
				continue
			ref = oref
			alt = alts[a]
			var_cov = var_covs[a]
			# left-align
			while ref[0] == alt[0]:
				ref = ref[1:]
				alt = alt[1:]
				position += 1
				if ref == '':
					ref = '-'
				if alt == '':
					alt = '-'
			# right-align
			while ref[-1] == alt[-1]:
				ref = ref[:-1]
				alt = alt[:-1]
				if ref == '':
					ref = '-'
				if alt == '':
					alt = '-'
			#Genomic Description (do not forget position has been modified with left-alignment)
			if ref == '-': # INS
				variant_type = 'INS'
				start = position - 1
				stop = position
				genomicDescription = '%s:g.%s_%sins%s' % (chromosome,start,stop,alt)
			elif alt == '-': # DEL
				variant_type = 'DEL'
				if len(ref) > 1:
					start = position
					stop = position + (len(ref)-1)
					genomicDescription = '%s:g.%s_%sdel%s' % (chromosome,start,stop,ref)
				else:
					start = position
					stop = position
					genomicDescription = '%s:g.%sdel%s' % (chromosome,start,ref)
			elif len(ref) > 1 or len(alt) > 1: # DELINS
				variant_type = 'DELINS'
				if len(ref) > 1:
					start = position
					stop = position + (len(ref)-1)
					genomicDescription = '%s:g.%s_%sdelins%s' % (chromosome,start,stop,alt)
				else:
					start = position
					stop = position
					genomicDescription = '%s:g.%sdelins%s' % (chromosome,start,alt)
			else: # SNP
				variant_type = 'SNP'
				start = position
				stop = position
				genomicDescription = '%s:g.%s%s>%s' % (chromosome,start,ref,alt)
				
			gene = ''
			for g in gene_data.keys():
				if chromosome == gene_data[g]['chromosome'] and ((gene_data[g]['transcriptionStart']-5000)<start<(gene_data[g]['transcriptionStop']+5000)):
					gene = str(g)
			
			#chrom = 'chr%0.2d' % int(chromosome.replace('chr',''))
			variant = '%s:%s-%s:%s>%s' % (chromosome,start,stop,ref,alt)
			if variant not in variants.keys():
				variants[variant] = {
					'chromosome':chromosome,
					'start':start,
					'stop':stop,
					'ref':ref,
					'alt':alt,
					'variant_type':variant_type,
					'gene':gene,
					'pos_cov':[pos_cov],
					'var_cov':[var_cov],
					'call':[vc_name],
					'genomicDescription':genomicDescription
				}
			elif vc_name not in variants[variant]['call']: # evite lofreq lignes en double...
				variants[variant]['pos_cov'].append(pos_cov)			
				variants[variant]['var_cov'].append(var_cov)			
				variants[variant]['call'].append(vc_name)			
				
#pprint(variants)
for variant in variants :
	print '%s\t%s\t%s\t%s\t%s' % (variants[variant]['gene'],variants[variant]['chromosome'],variants[variant]['start'],variants[variant]['ref'],variants[variant]['alt'])
print " - total all vcf : %s variants" % len(variants)
exit()

# IF ANALYSIS PREVIOUSLY DONE, DELETE OLD VARIANTMETRICS FIRST
db_cur.execute("SELECT variantMetricsID FROM VariantMetrics WHERE analysis='%s'" % analysis_id)
db_vms = db_cur.fetchall()
if db_vms:
	print " - [%s] analysisID already in DB : cleaning previous variantmetrics" % (time.strftime("%H:%M:%S"))
	for db_vm in db_vms:
		db_cur.execute("DELETE FROM VariantMetrics WHERE variantMetricsID='%s'" % db_vm['variantMetricsID'])
		
#print " - [%s] %s variants" % (time.strftime("%H:%M:%S"),len(variants.keys()))
#   ___                     __                __              ___    ___       __        ___ 
#  |__  | |    |    | |\ | / _`    \  /  /\  |__) |  /\  |\ |  |      |   /\  |__) |    |__  
#  |    | |___ |___ | | \| \__>     \/  /~~\ |  \ | /~~\ | \|  |      |  /~~\ |__) |___ |___ 

new_var_count = 0
new_vm_count = 0
for variant in variants:
	variant_id = variant
	pos_cov = int(mean(variants[variant]['pos_cov']))
	var_cov = int(mean(variants[variant]['var_cov']))
	call = ','.join(variants[variant]['call'])
	db_cur.execute("SELECT * FROM Variant WHERE variantID='%s'" % variant_id)
	db_variant = db_cur.fetchone()
	if db_variant is None:
		try:
			#print "- [Variant] : adding %s in DB" % variant_id
			db_cur.execute("INSERT INTO Variant (variantID, genomeBuild, chromosome, genomicStart, genomicStop, referenceAllele, alternativeAllele, variantType, gene, genomicDescription) VALUES ('%s','%s','%s',%s, %s,'%s','%s','%s','%s','%s')" % (variant_id, genome_build, variants[variant]['chromosome'], variants[variant]['start'], variants[variant]['stop'], variants[variant]['ref'], variants[variant]['alt'],variants[variant]['variant_type'],variants[variant]['gene'],variants[variant]['genomicDescription']))
			new_var_count += 1
		except Exception as e:
			print "\t - warning (VARIANT table)** %s"%e
	elif db_variant['hgvs'] == 'no':
		variant_id = db_variant['hgvsInfo']
				
	#   ___                     __                __              ___        ___ ___  __     __   __     ___       __        ___ 
	#  |__  | |    |    | |\ | / _`    \  /  /\  |__) |  /\  |\ |  |   |\/| |__   |  |__) | /  ` /__`     |   /\  |__) |    |__  
	#  |    | |___ |___ | | \| \__>     \/  /~~\ |  \ | /~~\ | \|  |   |  | |___  |  |  \ | \__, .__/     |  /~~\ |__) |___ |___ 
						
	db_cur.execute("SELECT variantMetricsID FROM VariantMetrics WHERE analysis='%s' and variant='%s'" % (analysis_id,variant_id))
	if db_cur.fetchone() is None:																					  
		random_uuid = uuid.uuid1()
		variantmetrics_id = 'M-'+random_uuid.hex[:8]
		try:
			#print "- [VariantMetrics] : adding %s in DB (%s <-> %s)" % (variantmetrics_id,variant_id,analysis_id)
			db_cur.execute("INSERT INTO VariantMetrics (variantMetricsID, variant, analysis, positionReadDepth, variantReadDepth, variantCallingTool, call) VALUES ('%s', '%s', '%s', %s, %s, '%s', '%s')" % (variantmetrics_id, variant_id, analysis_id, pos_cov, var_cov, vc_tool, call))
			new_vm_count += 1
		except Exception as e:
			print "\t - warning (VARIANTMETRICS table)** %s"%e

print " - [%s] %s variants (%s new entries, %s occurences added)" % (time.strftime("%H:%M:%S"),z,new_var_count,new_vm_count)

db_con.commit()
db_con.close()