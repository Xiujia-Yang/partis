#!/usr/bin/env python
import csv
import sys
import glob
import os
import re
from bs4 import BeautifulSoup

from opener import opener
import utils
import joinparser

from performanceplotter import PerformancePlotter

header_keys = {
    'V-GENE and allele':'v_gene',
    'D-GENE and allele':'d_gene',
    'J-GENE and allele':'j_gene',
    'V-REGION start':'v_start',  # NOTE these are *one* indexed, and *inclusive* of both endpoints
    'V-REGION end':'v_end',
    'D-REGION start':'d_start',
    'D-REGION end':'d_end',
    'J-REGION start':'j_start',
    'J-REGION end':'j_end'
}

class IMGTParser(object):
    # ----------------------------------------------------------------------------------------
    def __init__(self, seqfname, datadir, indir='', infname=''):
        self.debug = 1
        n_max_queries = -1
        queries = ['-3186447074198366744']

        self.germline_seqs = utils.read_germlines(datadir, remove_N_nukes=False)
        perfplotter = PerformancePlotter(self.germline_seqs, os.getenv('www') + '/partis/imgt_performance', 'imgt')

        # get info that was passed to joinsolver
        self.seqinfo = {}
        with opener('r')(seqfname) as seqfile:
            reader = csv.DictReader(seqfile)
            iline = 0
            for line in reader:
                if len(queries) > 0 and line['unique_id'] not in queries:
                    continue
                self.seqinfo[line['unique_id']] = line
                iline += 1
                if n_max_queries > 0 and iline >= n_max_queries:
                    break

        n_failed, n_total = 0, 0
        paragraphs, csv_info = None, None
        if '.html' in infname:
            with opener('r')(infname) as infile:
                soup = BeautifulSoup(infile)
                paragraphs = soup.find_all('pre')
        elif '.txt' in infname:
            with opener('r')(infname) as infile:
                reader = csv.DictReader(infile, delimiter='\t')
                csv_info = {}
                for line in reader:
                    csv_info[line['Sequence ID']] = line
        for unique_id in self.seqinfo:
            if self.debug:
                print unique_id,
            imgtinfo = []
            # print 'true'
            # utils.print_reco_event(self.germline_seqs, self.seqinfo[unique_id])
            if '.html' in infname:
                for pre in paragraphs:  # NOTE this loops over everything an awful lot of times. shouldn't really matter for now
                    if unique_id in pre.text:
                        imgtinfo.append(pre.text)
            elif '.txt' in infname:
                imgt_line = csv_info[unique_id]
                useful_line = {}
                assert False  # *sigh* I think there isn't actually enough info in here
                for imgt_key, key in header_keys.iteritems():
                    useful_line[key] = imgt_line[imgt_key]
            else:
                assert infname == ''
                infnames = glob.glob(indir + '/' + unique_id + '*')
                assert len(infnames) == 1
                with opener('r')(infnames[0]) as infile:
                    full_text = infile.read()
                    if len(re.findall('[123]. Alignment for [VDJ]-GENE', full_text)) < 3:
                        failregions = re.findall('No [VDJ]-GENE has been identified', full_text)
                        if len(failregions) > 0:
                            print '    ', failregions
                        n_failed += 1
                        continue
                    
                    # loop over the paragraphs I want
                    position = full_text.find(unique_id)  # don't need this one
                    for _ in range(4):
                        position = full_text.find(unique_id, position+1)
                        imgtinfo.append(full_text[position : full_text.find('\n\n', position+1)])  # query seq paragraph
            if len(imgtinfo) == 0:
                print '%s no info' % unique_id
                continue
            else:
                if self.debug:
                    print ''
            line = self.parse_query_text(unique_id, imgtinfo)
            n_total += 1
            if len(line) == 0:
                print '    giving up'
                n_failed += 1
                perfplotter.add_fail()
                continue
            joinparser.add_insertions(line)
            try:
                joinparser.resolve_overlapping_matches(line, debug=True)
            except AssertionError:
                print '    giving up'
                n_failed += 1
                perfplotter.add_fail()
                continue
            perfplotter.evaluate(self.seqinfo[unique_id], line, unique_id)
            if self.debug:
                utils.print_reco_event(self.germline_seqs, line)

        perfplotter.plot()
        print 'failed: %d / %d = %f' % (n_failed, n_total, float(n_failed) / n_total)

    # ----------------------------------------------------------------------------------------
    def parse_query_text(self, unique_id, query_info):
        if len(query_info) == 0:  # one for the query sequence, then one for v, d, and j
            print 'no info for',unique_id
            return {}
        elif len(query_info) < 4:
            regions_ok = ''
            for info in query_info:
                for region in utils.regions:
                    if 'IGH' + region.upper() in info:
                        regions_ok += region
            for region in utils.regions:
                if region not in regions_ok:
                    print '    ERROR no %s matches' % region
                    return {}
            assert False  # shouldn't get here
        elif len(query_info) != 4:
            print 'info for', unique_id, 'all messed up'
            for info in query_info:
                print info
            sys.exit()

        full_qr_seq = query_info[0].replace('>', '').replace(unique_id, '')  # strip off the unique id
        full_qr_seq = ''.join(full_qr_seq.split()).upper()  # strip off white space and uppercase it
        assert full_qr_seq == self.seqinfo[unique_id]['seq']

        line = {}
        line['unique_id'] = unique_id
        line['seq'] = full_qr_seq
        # qrbounds = {}
        for ireg in range(len(utils.regions)):
            region = utils.regions[ireg]
            info = query_info[ireg + 1].splitlines()
            if unique_id not in info[0]:  # remove the line marking cdr3 and framework regions
                info.pop(0)
            if len(info) <= 1:
                print info
            assert len(info) > 1
            assert len(info[0].split()) == 2
            qr_seq = info[0].split()[1].upper()  # this line should be '<unique_id> .............<query_seq>'
            match_name = str(info[1].split()[2])
            gl_seq = info[1].split()[4].upper()
            assert qr_seq.replace('.', '') in self.seqinfo[unique_id]['seq']

            if self.debug:
                print '  ', region, match_name
                print '    qr', qr_seq
                print '      ', gl_seq

            # replace the dots (gaps) in the gl match
            new_qr_seq, new_gl_seq = [], []
            for inuke in range(min(len(qr_seq), len(gl_seq))):
                if gl_seq[inuke] == '.':
                    pass
                else:
                    new_qr_seq.append(qr_seq[inuke])  # this should only be out of range if the v match extends through the whole query sequence, i.e. friggin never
                    new_gl_seq.append(gl_seq[inuke])
            for inuke in range(len(gl_seq), len(qr_seq)):
                new_qr_seq.append(qr_seq[inuke])
            for inuke in range(len(qr_seq), len(gl_seq)):
                new_gl_seq.append(gl_seq[inuke])
            qr_seq = ''.join(new_qr_seq)
            gl_seq = ''.join(new_gl_seq)

            # work out the erosions
            qr_ldots = qr_seq.rfind('.') + 1  # first strip off any dots on the left of query seq
            qr_seq = qr_seq[qr_ldots : ]
            gl_seq = gl_seq[qr_ldots : ]
            gl_ldots = gl_seq.rfind('.') + 1  # then remove dots on the left of the germline seq
            qr_seq = qr_seq[gl_ldots : ]
            gl_seq = gl_seq[gl_ldots : ]
            del_5p = qr_ldots + gl_ldots
            qr_seq = qr_seq[ : len(gl_seq)]  # then strip the right-hand portion of the query sequence that isn't aligned to the germline
            del_3p = len(gl_seq) - len(qr_seq)  # then do the same for the germline overhanging on the right of the query
            gl_seq = gl_seq[ : len(qr_seq)]
            assert len(gl_seq) == len(qr_seq)
            new_gl_seq = []
            for inuke in range(len(gl_seq)):  # replace dashes (matched bases)
                assert gl_seq[inuke] != '.'  # hoping there's no gaps in here
                if gl_seq[inuke] == '-':
                    new_gl_seq.append(qr_seq[inuke])
                else:
                    new_gl_seq.append(gl_seq[inuke])
            gl_seq = ''.join(new_gl_seq)
            if self.debug:
                print '    qr', qr_seq
                print '      ', gl_seq, del_5p, del_3p

            # if len(re.findall(qr_seq, full_qr_seq)) != 1:
            #     print region, re.findall(qr_seq, full_qr_seq)
            # assert len(re.findall(qr_seq, full_qr_seq)) == 1
            # qr_start = full_qr_seq.find(qr_seq)
            # assert qr_start >= 0
            # qrbounds[region] = (qr_start, qr_start + len(qr_seq))
            try:
                match_name = joinparser.figure_out_which_damn_gene(self.germline_seqs, match_name, gl_seq, debug=self.debug)
            except:
                print 'ERROR couldn\'t figure out the gene for %s' % match_name
                return {}

            # # remove the extra righthand bases in the imgt version
            # NOTE downloaded the imgt j versions, so I shouldn't need this any more
            # adaptive_gl_seq = self.germline_seqs[region][match_name]
            # if region == 'j':
            #     if adaptive_gl_seq[del_5p : ].find(gl_seq) != 0:  # left hand side of the two should be the same now
            #         return {}  # if it isn't, imgt kicked up a nonsense match
            #     assert len(adaptive_gl_seq[del_5p : ]) == len(gl_seq)  # should be ok for now
            #     del_3p = 0
            # # ad-hoc in extra deletions that don't show up in imgt's format (@&^##!!)
            # extra_5p_del = adaptive_gl_seq[del_5p : ].find(gl_seq)
            # if extra_5p_del > 0:
            #     del_5p += extra_5p_del
            #     if self.debug:
            #         print '    WARNING jacking in an extra 5p deletion of %d' % extra_5p_del
            # if gl_seq != adaptive_gl_seq[del_5p : len(adaptive_gl_seq) - del_3p]:
            #     print 'ERROR %s doesn\'t match adaptive gl version' % match_name
            #     print 'imgt              ', gl_seq
            #     print 'adaptive          ', adaptive_gl_seq[del_5p : len(adaptive_gl_seq) - del_3p]
            #     print 'adaptive untrimmed', adaptive_gl_seq
            #     for info in query_info:
            #         print info
            #     sys.exit()
            line[region + '_gene'] = match_name
            line[region + '_qr_seq'] = qr_seq
            line[region + '_gl_seq'] = gl_seq
            line[region + '_5p_del'] = del_5p
            line[region + '_3p_del'] = del_3p
            
        return line
# joinparser.figure_out_which_damn_gene(self.germline_seqs, 
#             if match_names[region] not in self.germline_seqs[region]:
#                 print 'ERROR %s not found in germline file' % match_names[region]
#                 sys.exit()

# iparser = IMGTParser('caches/recombinator/simu.csv', datadir='./data/imt', infname='/home/dralph/Dropbox/imgtvquest.html')
# iparser = IMGTParser('caches/recombinator/longer-reads/simu.csv', datadir='data/imgt', indir='performance/imgt/foop3/IMGT_HighV-QUEST_individual_files_folder')
iparser = IMGTParser('caches/recombinator/longer-reads/simu.csv', datadir='data/imgt', indir='performance/imgt/foop3/3_Nt-sequences_foop3_311014.txt')
