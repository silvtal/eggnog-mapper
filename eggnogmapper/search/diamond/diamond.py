##
## CPCantalapiedra 2019

from os.path import join as pjoin
import shutil
import subprocess
from tempfile import mkdtemp
import uuid

from ...emapperException import EmapperException
from ...common import DIAMOND, get_eggnog_dmnd_db, get_call_info, ITYPE_CDS, ITYPE_PROTS, ITYPE_GENOME, ITYPE_META
from ...utils import colorify

from ..hmmer.hmmer_seqio import iter_fasta_seqs

SENSMODE_FAST = "fast"
SENSMODE_MID_SENSITIVE = "mid-sensitive"
SENSMODE_SENSITIVE = "sensitive"
SENSMODE_MORE_SENSITIVE = "more-sensitive"
SENSMODE_VERY_SENSITIVE = "very-sensitive"
SENSMODE_ULTRA_SENSITIVE = "ultra-sensitive"
# sens modes in diamond 0.9.24
# SENSMODES = [SENSMODE_FAST, SENSMODE_SENSITIVE, SENSMODE_MORE_SENSITIVE]
# sens modes in diamond 2.0.4
SENSMODES = [SENSMODE_FAST, SENSMODE_MID_SENSITIVE, SENSMODE_SENSITIVE, SENSMODE_MORE_SENSITIVE, SENSMODE_VERY_SENSITIVE, SENSMODE_ULTRA_SENSITIVE]

class DiamondSearcher:

    name = "diamond"
    
    # Command
    cpu = tool = dmnd_db = temp_dir = no_file_comments = None
    matrix = gapopen = gapextend = None

    # Filters
    pident_thr = score_thr = evalue_thr = query_cov = subject_cov = excluded_taxa = None

    in_file = None
    itype = None

    # Results
    queries = hits = no_hits = None

    ##
    def __init__(self, args):
        
        self.itype = args.itype

        self.dmnd_db = args.dmnd_db if args.dmnd_db else get_eggnog_dmnd_db()

        self.cpu = args.cpu

        self.sensmode = args.sensmode
        
        self.query_cov = args.query_cover
        self.subject_cov = args.subject_cover

        self.matrix = args.matrix
        self.gapopen = args.gapopen
        self.gapextend = args.gapextend

        self.pident_thr = args.pident
        self.evalue_thr = args.dmnd_evalue
        self.score_thr = args.dmnd_score
        self.excluded_taxa = args.excluded_taxa if args.excluded_taxa else None
        
        self.temp_dir = args.temp_dir
        self.no_file_comments = args.no_file_comments
        
        return

    ##
    def search(self, in_file, seed_orthologs_file, hits_file = None):
        # DiamondSearcher does not use the "hits_file"
        # but we need this wrapper to define the search interface for Emapper
        return self._search(in_file, seed_orthologs_file)

    def _search(self, in_file, seed_orthologs_file):
        if not DIAMOND:
            raise EmapperException("%s command not found in path" % (DIAMOND))

        self.in_file = in_file
        
        tempdir = mkdtemp(prefix='emappertmp_dmdn_', dir=self.temp_dir)
        try:
            output_file = pjoin(tempdir, uuid.uuid4().hex)
            cmd = self.run_diamond(in_file, output_file)
            self.hits = self.parse_diamond(output_file)            
            self.output_diamond(cmd, self.hits, seed_orthologs_file)

        except Exception as e:
            raise e
        finally:
            shutil.rmtree(tempdir)
            
        return

    ##
    def get_hits(self):
        return self.hits

    ##
    def get_no_hits(self):
        if self.hits is not None:
            hit_queries = set([x[0] for x in self.hits])
            self.queries = set({name for name, seq in iter_fasta_seqs(self.in_file)})
            self.no_hits = set(self.queries).difference(hit_queries)
            
        return self.no_hits

    ##
    def run_diamond(self, fasta_file, output_file):
        if self.itype == ITYPE_CDS or self.itype == ITYPE_GENOME or self.itype == ITYPE_META:
            tool = 'blastx'
        elif self.itype == ITYPE_PROTS:
            tool = 'blastp'
        else:
            raise EmapperException(f"Unrecognized --itype {self.itype}.")
        
        if self.sensmode == SENSMODE_FAST:
            cmd = (
                f'{DIAMOND} {tool} -d {self.dmnd_db} -q {fasta_file} '
                f'--threads {self.cpu} -e {self.evalue_thr} -o {output_file} '
                f'--query-cover {self.query_cov} --subject-cover {self.subject_cov}'
            )
        else:
            cmd = (
                f'{DIAMOND} {tool} -d {self.dmnd_db} -q {fasta_file} '
                f'--{self.sensmode} --threads {self.cpu} -e {self.evalue_thr} -o {output_file} '
                f'--query-cover {self.query_cov} --subject-cover {self.subject_cov}'
            )

        if self.matrix: cmd += ' --matrix {self.matrix}'
        if self.gapopen: cmd += ' --gapopen {self.gapopen}'
        if self.gapextend: cmd += ' --gapextend {self.gapextend}'

        if self.itype == ITYPE_CDS or self.itype == ITYPE_PROTS:
            if self.excluded_taxa:
                cmd += " --max-target-seqs 25 "
            else:
                cmd += " --top 3 "
        else: # self.itype == ITYPE_GENOME or self.itype == ITYPE_META:
            cmd += " --max-target-seqs 0 --max-hsps 0 "

        print(colorify('  '+cmd, 'yellow'))
        try:
            completed_process = subprocess.run(cmd, capture_output=True, check=True, shell=True)
        except subprocess.CalledProcessError as cpe:
            raise EmapperException("Error running diamond: "+cpe.stderr.decode("utf-8").strip().split("\n")[-1])

        return cmd


    ##
    def parse_diamond(self, raw_dmnd_file):
        if self.itype == ITYPE_CDS or self.itype == ITYPE_PROTS:
            return self._parse_diamond(raw_dmnd_file)
        else: #self.itype == ITYPE_GENOME or self.itype == ITYPE_META:
            return self._parse_genepred(raw_dmnd_file)
        
    ##
    def _parse_diamond(self, raw_dmnd_file):
        hits = []

        visited = set()
        with open(raw_dmnd_file, 'r') as raw_f:
            for line in raw_f:
                if not line.strip() or line.startswith('#'):
                    continue

                fields = list(map(str.strip, line.split('\t')))
                # From diamond docs:
                # By default, there are 12 preconfigured fields: qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore
                query = fields[0]

                if query in visited:
                    continue
                
                pident = float(fields[2])
                evalue = float(fields[10])
                score = float(fields[11])
                
                if pident < self.pident_thr or evalue > self.evalue_thr or score < self.score_thr:
                    continue

                hit = fields[1]
                
                if self.excluded_taxa and hit.startswith("%s." % self.excluded_taxa):
                    continue

                visited.add(query)

                hits.append([query, hit, evalue, score])
            
        return hits

        ##
    def _parse_genepred(self, raw_dmnd_file):
        hits = []

        visited = set()
        with open(raw_dmnd_file, 'r') as raw_f:
            for line in raw_f:
                if not line.strip() or line.startswith('#'):
                    continue

                fields = list(map(str.strip, line.split('\t')))
                # From diamond docs:
                # By default, there are 12 preconfigured fields: qseqid sseqid pident length mismatch gapopen qstart qend sstart send evalue bitscore
                pident = float(fields[2])
                evalue = float(fields[10])
                score = float(fields[11])
                
                if pident < self.pident_thr or evalue > self.evalue_thr or score < self.score_thr:
                    continue

                hit = fields[1]
                if self.excluded_taxa and hit.startswith("%s." % self.excluded_taxa):
                    continue

                query = fields[0]
                qstart = int(fields[6])
                qend = int(fields[7])
                sstart = int(fields[8])
                send = int(fields[9])
                
                hit = [query, hit, evalue, score, qstart, qend, sstart, send]

                if not self._does_overlap(hit, hits):
                    hits.append(hit)
            
        return hits

    def _does_overlap(self, hit, hits):
        does_overlap = False
        
        hitstart = hit[4]
        hitend = hit[5]

        for o in hits:
            ostart = o[4]
            oend = o[5]
            overlap = None
            if hitend <= ostart:
                overlap = hitend - ostart
            elif hitstart >= oend:
                overlap = oend - hitstart
            else:
                overlap_start = max(hitstart, ostart)
                overlap_end = min(hitend, oend)
                overlap = overlap_end - (overlap_start - 1)

            if overlap > 0:
                does_overlap = True
                break
                
        return does_overlap


    ##
    def output_diamond(self, cmd, hits, out_file):
        if self.itype == ITYPE_CDS or self.itype == ITYPE_PROTS:
            return self._output_diamond(cmd, hits, out_file)
        else: #self.itype == ITYPE_GENOME or self.itype == ITYPE_META:
            return self._output_genepred(cmd, hits, out_file)
        
    ##
    def _output_diamond(self, cmd, hits, out_file):
        with open(out_file, 'w') as OUT:
        
            if not self.no_file_comments:
                print(get_call_info(), file=OUT)
                print('#'+cmd, file=OUT)

            for line in hits:
                print('\t'.join(map(str, line)), file=OUT)
                
        return

    ##
    def _output_genepred(self, cmd, hits, out_file):
        queries_suffixes = {}
        with open(out_file, 'w') as OUT:
        
            if not self.no_file_comments:
                print(get_call_info(), file=OUT)
                print('#'+cmd, file=OUT)

            for line in hits:
                query = line[0]
                target = line[1]
                evalue = line[2]
                score = line[3]
                if query in queries_suffixes:
                    queries_suffixes[query] += 1
                    suffix = queries_suffixes[query]
                else:
                    suffix = 0
                    queries_suffixes[query] = suffix
                    
                print('\t'.join(map(str, [f"{query}_{suffix}", target, str(evalue), str(score)])), file=OUT)
                
        return

## END