import sys
import string
import re
import random
import numpy
import copy

import utils

# ----------------------------------------------------------------------------------------
def get_empty_indel():
    return {'reversed_seq' : '', 'indels' : [], 'genes' : {}, 'qr_gap_seq' : '', 'gl_gap_seq' : ''}  # it would be nice to eventually just have the gap seqs and bool in the <line>, but it's difficult to switch over (I tried)

# ----------------------------------------------------------------------------------------
def has_indels(indelfo):
    return len(indelfo['indels']) > 0

# ----------------------------------------------------------------------------------------
def sign(ifo):
    if ifo['type'] == 'insertion':
        return 1
    elif ifo['type'] == 'deletion':
        return -1
    else:
        assert False

# ----------------------------------------------------------------------------------------
def net_length(indelfo):
    return sum([sign(ifo) * ifo['len'] for ifo in indelfo['indels']])

# ----------------------------------------------------------------------------------------
def adjust_single_position_for_reinstated_indels(indel, position):
    if indel['pos'] > position:  # NOTE this just ignores the case where the indel's in the middle of the codon, because, like, screw that I don't want to think about it
        return position
    if indel['type'] == 'insertion':
        return position + indel['len']
    elif indel['type'] == 'deletion':
        return position - indel['len']
    else:
        assert False

# ----------------------------------------------------------------------------------------
def get_codon_positions_with_indels_reinstated(line, iseq, codon_positions):
    # NOTE as long as the indels are reversed, all the sequences have the same codon positions. But as soon as we reinstate the indels, all heck breaks loose.
    indelfo = line['indelfos'][iseq]
    reinstated_codon_positions = copy.deepcopy(codon_positions)
    if not has_indels(indelfo):
        return reinstated_codon_positions

    for indel in indelfo['indels']:
        for region in reinstated_codon_positions:
            reinstated_codon_positions[region] = adjust_single_position_for_reinstated_indels(indel, reinstated_codon_positions[region])
    return reinstated_codon_positions

# ----------------------------------------------------------------------------------------
def get_regional_bounds_with_indels_reinstated(line, iseq):
    indelfo = line['indelfos'][iseq]
    regional_bounds = copy.deepcopy(line['regional_bounds'])
    if not has_indels(indelfo):
        return regional_bounds

    for indel in indelfo['indels']:
        for region in regional_bounds:
            regional_bounds[region] = (adjust_single_position_for_reinstated_indels(indel, regional_bounds[region][0]),
                                       adjust_single_position_for_reinstated_indels(indel, regional_bounds[region][1]))
    return regional_bounds

# ----------------------------------------------------------------------------------------
def get_qr_seqs_with_indels_reinstated(line, iseq):
    rbounds = get_regional_bounds_with_indels_reinstated(line, iseq)
    # assert line['input_seqs'][iseq] == get_seq_with_indels_reinstated(line, iseq)
    inseq = line['input_seqs'][iseq]
    qr_seqs = {r : inseq[rbounds[r][0] : rbounds[r][1]] for r in utils.regions}
    return qr_seqs

# ----------------------------------------------------------------------------------------
def add_indels(n_indels, qrseq, glseq, mean_length, codon_positions, indel_location=None, indel_positions=None, keep_in_frame=False, dbg_pad=0, debug=False):
    def getpos():  # if <pos> is specified we use that, otherwise we use <indel_location> to decide the region of the sequence from which to choose a position
        if indel_location is None:  # uniform over entire sequence
            return random.randint(5, len(qrseq) - 6)  # this will actually exclude either before the first index or after the last index. No, I don't care.
        elif indel_location == 'v':  # within the meat of the v
            return random.randint(5, codon_positions['v'])  # NOTE this isn't actually right, since the codon positions get modified as we add each indel... but it won't usually make a difference
        elif indel_location == 'cdr3':  # inside cdr3
            return random.randint(codon_positions['v'], codon_positions['j'])
        else:
            assert False
    def getlen():
        length = numpy.random.geometric(1. / mean_length)
        if keep_in_frame:
            itry = 0
            while length % 3 != 0:
                length = numpy.random.geometric(1. / mean_length)
                itry += 1
                if itry > 9999:
                    raise Exception('tried too many times to get in-frame indel length')
        return length
    def overlaps(pos, length):  # see if there's any existing indels close to where we're thinking of putting this one NOTE in practice this _really_ shouldn't happen much -- there should be only a a couple of indels per sequence at most -- this just keeps other things (e.g. indelfo consistency checks) from getting confused and crashing
        for gapseq in (indelfo['qr_gap_seq'], indelfo['gl_gap_seq']):
            if len(gapseq) < pos + length + 1:
                return True
            if utils.gap_len(gapseq[pos - length : pos + length]) > 0:  # this leaves a pretty, albeit inexact, large buffer
                return True
        return False

    # choose positions and lengths
    if indel_positions is None:
        indel_positions = [None for _ in range(n_indels)]
    if debug:
        print '%sadding %d indel%s' % (dbg_pad * ' ', n_indels, utils.plural(n_indels))

    # then build the indelfo
    indelfo = get_empty_indel()
    indelfo['genes'] = {}  # it's kind of awkward to have the match info here, but I need some way to pasp it between the aligner that's calling the indel (typically vsearch) and the aligner that's using it (typically sw)
    indelfo['qr_gap_seq'], indelfo['gl_gap_seq'] = qrseq, glseq
    indelfo['reversed_seq'] = qrseq
    for pos in indel_positions:
        length = getlen()
        while pos is None or overlaps(pos, length):
            pos = getpos()
        add_single_indel(indelfo, pos, length, codon_positions, keep_in_frame=keep_in_frame, debug=debug)

    # make the "input seq", i.e. without gaps, and account for this in the codon positions
    input_seq = filter(utils.alphabet.__contains__, indelfo['qr_gap_seq'])
    for region in codon_positions:
        codon_positions[region] -= utils.count_gap_chars(indelfo['qr_gap_seq'], aligned_pos=codon_positions[region])

    if debug:
        print utils.pad_lines(get_dbg_str(indelfo), dbg_pad + 4)

    return input_seq, indelfo

# ----------------------------------------------------------------------------------------
def add_single_indel(indelfo, pos, length, gapped_codon_positions, keep_in_frame=False, debug=False):
    ifo = {'type' : None, 'pos' : pos, 'len' : length, 'seqstr' : None}
    if numpy.random.uniform(0, 1) < 0.5:  # fifty-fifty chance of insertion and deletion
        ifo['type'] = 'insertion'
        ifo['seqstr'] = ''.join([utils.nukes[random.randint(0, len(utils.nukes) - 1)] for _ in range(length)])
        if utils.gap_len(ifo['seqstr']) > 0:  # this is a backup for the uncommon cases where overlaps() in the calling fcn doesn't catch something
            print '  failed adding indel (overlaps with previous one)'
            return
        indelfo['qr_gap_seq'] = indelfo['qr_gap_seq'][:pos] + ifo['seqstr'] + indelfo['qr_gap_seq'][pos:]
        indelfo['gl_gap_seq'] = indelfo['gl_gap_seq'][:pos] + length * utils.gap_chars[0] + indelfo['gl_gap_seq'][pos:]
        for region in gapped_codon_positions:
            if pos < gapped_codon_positions[region]:  # this isn\'t right if the indel is actually in the codon, but in that case we just let the messed up codon through below
                gapped_codon_positions[region] += length
        for otherfo in indelfo['indels']:  # correct the positions of any existing indels that're to the right of this one
            if otherfo['pos'] > pos:
                otherfo['pos'] += ifo['len']
    else:
        ifo['type'] = 'deletion'
        ifo['seqstr'] = indelfo['gl_gap_seq'][pos : pos + length]  # NOTE it's kind of unclear whether this should be the bit in the qr or gl seq. Using the gl like this probably makes more sense, since it corresponds to what we would infer in s-w (i.e., if we _do_ delete some SHMd positions, we will never know about it, so who cares)
        if utils.gap_len(ifo['seqstr']) > 0:  # this is a backup for the uncommon cases where overlaps() in the calling fcn doesn't catch something
            print '  failed adding indel (overlaps with previous one)'
            return
        indelfo['qr_gap_seq'] = indelfo['qr_gap_seq'][:pos] + length * utils.gap_chars[0] + indelfo['qr_gap_seq'][pos + length : ]

    if not utils.codon_unmutated('cyst', indelfo['qr_gap_seq'], gapped_codon_positions['v']):
        if debug:
            print '  adding indel within %s codon' % 'cyst'

    indelfo['indels'].append(ifo)
    indelfo['indels'] = sorted(indelfo['indels'], key=lambda q: q['pos'])

    if debug:
        print get_dbg_str(indelfo)

# ----------------------------------------------------------------------------------------
def color_cigar(cigarstr):
    return ''.join([utils.color('bold', utils.color('blue', c)) if c in 'MIDS' else c for c in cigarstr])

# ----------------------------------------------------------------------------------------
def split_cigarstr(cstr):
    assert len(cstr) > 0
    code = cstr[-1]
    if code not in 'MIDS':
        raise Exception('unhandled cigar code %s' % code)
    lstr = cstr[:-1] if len(cstr) > 1 else '1'  # stupid vsearch doesn't write the 1 (e.g. 'D' instead of '1D')
    return code, int(lstr)

# ----------------------------------------------------------------------------------------
def get_dbg_str(indelfo):
    if len(indelfo['qr_gap_seq']) != len(indelfo['gl_gap_seq']):
        print indelfo['qr_gap_seq']
        print indelfo['gl_gap_seq']
        raise Exception('different length qr and gl gap seqs (see previous lines)')
    qrprintstr, glprintstr = [], []
    for ich in range(len(indelfo['qr_gap_seq'])):
        qrb, glb = indelfo['qr_gap_seq'][ich], indelfo['gl_gap_seq'][ich]
        qrcolor, glcolor = None, None
        if qrb in utils.gap_chars or glb in utils.gap_chars:
            qrcolor = 'light_blue'
            glcolor = 'light_blue'
        elif qrb in utils.ambiguous_bases:
            qrcolor = 'light_blue'
        elif glb in utils.ambiguous_bases:
            glcolor = 'light_blue'
        elif qrb != glb:
            qrcolor = 'red'
        qrprintstr.append(utils.color(qrcolor, qrb if qrb not in utils.gap_chars else '*'))  # change it to a start just cause that's what it originally was... at some point should switch to just leaving it whatever gap char it was
        glprintstr.append(utils.color(glcolor, glb if glb not in utils.gap_chars else '*'))
    qrprintstr = ''.join(qrprintstr)
    glprintstr = ''.join(glprintstr)

    gene_str = ''
    gwidth = str(len('query'))
    if 'v' in indelfo['genes']:
        gene_str = utils.color_gene(indelfo['genes']['v'], width=int(gwidth), leftpad=True)
        gwidth = str(utils.len_excluding_colors(gene_str))
    dj_gene_str = ' '.join([utils.color_gene(indelfo['genes'][r]) for r in 'dj' if r in indelfo['genes']])
    dbg_str_list = [('  %' + gwidth + 's  %s  %s') % (gene_str, glprintstr, dj_gene_str),
                    ('  %' + gwidth + 's  %s') % ('query', qrprintstr)]
    for idl in indelfo['indels']:
        dbg_str_list.append('%10s: %d base%s at %d (%s)' % (idl['type'], idl['len'], utils.plural(idl['len']), idl['pos'], idl['seqstr']))
    return '\n'.join(dbg_str_list)

# ----------------------------------------------------------------------------------------
def get_reversed_seq(qr_gap_seq, gl_gap_seq, v_5p_del_str, j_3p_del_str):
    reversed_match_seq = [(qrb if qrb not in utils.gap_chars else glb) for qrb, glb in zip(qr_gap_seq, gl_gap_seq) if glb not in utils.gap_chars]
    return v_5p_del_str + ''.join(reversed_match_seq) + j_3p_del_str

# ----------------------------------------------------------------------------------------
def check_cigar_len(cigars, qrseq, glseq, uid=None):  # check consistency between cigar and qr/gl seqs
    for seqtype, tmpseq, tmpcode in (('qr', qrseq, 'D'), ('gl', glseq, 'I')):
        cigar_len = sum([length for code, length in cigars if code != tmpcode])
        if cigar_len != len(tmpseq):
            raise Exception('cigar length %d doesn\'t match %s seq length %d%s' % (cigar_len, seqtype, len(tmpseq), (' for %s' % uid) if uid is not None else ''))

# ----------------------------------------------------------------------------------------
def get_indelfo_from_cigar_and_line(cigarstr, line, iseq, debug=False):
    return get_indelfo_from_cigar(cigarstr,
                                  line['input_seqs'][iseq], (0, len(line['input_seqs'][iseq])),
                                  line['naive_seq'], (0, len(line['naive_seq'])),
                                  {r : line[r + '_gene'] for r in utils.regions},
                                  uid=line['unique_ids'][iseq], debug=debug)

# ----------------------------------------------------------------------------------------
def get_indelfo_from_cigar(cigarstr, full_qrseq, qrbounds, full_glseq, glbounds, genes, vsearch_conventions=False, uid=None, debug=False):
    # debug = 'D' in cigarstr or 'I' in cigarstr
    if debug:
        print '  initial%s:' % ((' for %s' % uid) if uid is not None else '')
        print '    %s' % color_cigar(cigarstr)
        print '    qr %3d %3d %s' % (qrbounds[0], qrbounds[1], full_qrseq)
        print '    gl %3d %3d %s' % (glbounds[0], glbounds[1], full_glseq)

    cigars = [split_cigarstr(cstr) for cstr in re.findall('[0-9]*[A-Z]', cigarstr)]  # split cigar string into its parts, then split each part into the code and the length 
    if vsearch_conventions:
        assert 'v' in genes  # would need to be generalized
        cigars = [(code.translate(string.maketrans('ID', 'DI')), length) for code, length in cigars]  # vsearch reverses what's the query and what's the target/gene/whathaveyou compared to what ig-sw does
        for iend in [0, -1]:
            if cigars[iend][0] == 'I':  # qr extends beyond gl: ig-sw calls these soft-clips, vsearch calls them insertions
                cigars[iend] = ('S', cigars[iend][1])
            elif cigars[iend][0] == 'D':  # gl goes past qr: ig-sw just calls them not part of the alignment, vsearch calls them deletions
                cigars.pop(iend)
    cigars = [(code, length) for code, length in cigars if code != 'S']  # remove soft-clipping
    cigarstr = ''.join(['%d%s' % (l, c) for c, l in cigars])
    qrseq = full_qrseq[qrbounds[0] : qrbounds[1]]  # ...and trim qrseq and glseq
    glseq = full_glseq[glbounds[0] : glbounds[1]]

    if debug:
        print '  parsed:'
        print '    %s' % color_cigar(cigarstr)
        print '    %s' % '   '.join(['%s %d' % (c, l) for c, l in cigars])
        print '    qr %s' % qrseq
        print '    gl %s' % glseq

    check_cigar_len(cigars, qrseq, glseq, uid=uid)

    indelfo = get_empty_indel()  # replacement_seq: query seq with insertions removed and germline bases inserted at the position of deletions
    if 'I' not in cigarstr and 'D' not in cigarstr:  # has to happen after we've changed from vsearch conventions
        if debug:
            print '  no indels'
        return indelfo

    # each position is the cigar code corresponding to that position in the alignment
    codestr = ''.join([length * code for code, length in cigars])

    # add each indel to <indelfo['indels']>, and build <tmp_indices> to keep track of what's going on at each position
    indel_pos = 0  # position within alignment (god damnit, I used to have written here that it was the query sequence position)
    tmp_indices = []  # integer for each position in the alignment, giving the index of the indel that we're within (None if we're not in an indel)
    if debug:
        print '      code  length'
    for code, length in cigars:
        if debug:
            print '        %s     %3d' % (code, length)
        if code == 'I':  # advance qr seq but not gl seq
            indelfo['indels'].append({'type' : 'insertion', 'pos' : indel_pos, 'len' : length, 'seqstr' : []})  # insertion begins at <pos> (note that 'seqstr' later on gets converted from a list to a string)
            tmp_indices += [len(indelfo['indels']) - 1  for _ in range(length)]  # indel index corresponding to this position in the alignment
        elif code == 'D':  # advance qr seq but not gl seq
            indelfo['indels'].append({'type' : 'deletion', 'pos' : indel_pos, 'len' : length, 'seqstr' : []})  # first deleted base is <pos> (well, first base which is in the position of the first deleted base)
            tmp_indices += [len(indelfo['indels']) - 1  for _ in range(length)]  # indel index corresponding to this position in the alignment
        else:
            tmp_indices += [None  for _ in range(length)]  # indel index corresponding to this position in the alignment
        indel_pos += length

    if debug:
        print '      %s  codestr' % ''.join([c if c not in 'ID' else utils.color('blue', c) for c in codestr])
        print '      %s  indel index' % ''.join([str(ti if ti is not None else ' ') for ti in tmp_indices])

    # then construct the dbg strings, indel-reversed input sequence, and 'seqstr' entries in indelfo
    qr_gap_seq, gl_gap_seq = [], []
    iqr, igl = 0, 0
    for icode in range(len(codestr)):
        code = codestr[icode]
        qrb, glb = qrseq[iqr], glseq[igl]
        if code == 'M':
            qr_gap_seq += [qrb]
            gl_gap_seq += [glb]
        elif code == 'I':
            indelfo['indels'][tmp_indices[icode]]['seqstr'] += [qrb]  # and to the sequence of just this indel
            qr_gap_seq += [qrb]
            gl_gap_seq += ['.']
            igl -= 1
        elif code == 'D':
            indelfo['indels'][tmp_indices[icode]]['seqstr'] += [glb]  # and to the sequence of just this indel
            qr_gap_seq += ['.']
            gl_gap_seq += [glb]
            iqr -= 1
        else:
            raise Exception('unexpected cigar code %s' % code)
        iqr += 1
        igl += 1

    # convert character lists to strings (indels are rare enough that this probably isn't that much faster, but it just feels wrong not to)
    qr_gap_seq = ''.join(qr_gap_seq)
    gl_gap_seq = ''.join(gl_gap_seq)
    for ifo in indelfo['indels']:
        ifo['seqstr'] = ''.join(ifo['seqstr'])

    # at the start of this fcn we trimmed off the "non-matched" bits of the query and germline sequences, so now we have to account for them (it might be nicer to have it all done at once, but this is the way it is, for historical reasons) (where the definition of "non-matched" is a bit fuzzy depending on whether it's vsearch or ig-sw)
    for ifo in indelfo['indels']:
        ifo['pos'] += qrbounds[0]

    # NOTE gapped seqs do _not_ contain the v 5p and j 3p deletions or fv and jf insertions, because this makes it easier to combine indels from different regions later on
    indelfo['genes'] = genes
    indelfo['qr_gap_seq'] = qr_gap_seq
    indelfo['gl_gap_seq'] = gl_gap_seq
    indelfo['reversed_seq'] = get_reversed_seq(qr_gap_seq, gl_gap_seq, full_qrseq[ : qrbounds[0]], full_qrseq[qrbounds[1] : ])

    if debug:
        print utils.pad_lines(get_dbg_str(indelfo), 0)

    return indelfo

# ----------------------------------------------------------------------------------------
def pad_indel_info_in_line(line, iseq, leftstr, rightstr):  # TODO holy fucking shit don't have both of these fcns
    # TODO update for any new keys
    indelfo = line['indelfos'][iseq]
    indelfo['qr_gap_seq'] = leftstr + indelfo['qr_gap_seq'] + rightstr
    indelfo['gl_gap_seq'] = leftstr + indelfo['gl_gap_seq'] + rightstr
    indelfo['reversed_seq'] = leftstr + indelfo['reversed_seq'] + rightstr
    for indel in indelfo['indels']:
        indel['pos'] += len(leftstr)

# ----------------------------------------------------------------------------------------
def pad_indelfo(indelfo, leftstr, rightstr):  # TODO holy fucking shit don't have both of these fcns
    # TODO update for any new keys
    indelfo['qr_gap_seq'] = leftstr + indelfo['qr_gap_seq'] + rightstr
    indelfo['gl_gap_seq'] = leftstr + indelfo['gl_gap_seq'] + rightstr
    indelfo['reversed_seq'] = leftstr + indelfo['reversed_seq'] + rightstr
    for indel in indelfo['indels']:
        indel['pos'] += len(leftstr)

# ----------------------------------------------------------------------------------------
def trim_indel_info(line, iseq, fv_insertion_to_remove, jf_insertion_to_remove, v_5p_to_remove, j_3p_to_remove):
    for skey in ['qr_gap_seq', 'gl_gap_seq']:
        istart = len(fv_insertion_to_remove) + v_5p_to_remove
        istop = len(line['indelfos'][iseq][skey]) - len(jf_insertion_to_remove) - j_3p_to_remove
        line['indelfos'][iseq][skey] = line['indelfos'][iseq][skey][istart : istop]

    rseq = line['indelfos'][iseq]['reversed_seq']
    rseq = rseq[len(fv_insertion_to_remove) + v_5p_to_remove : ]
    if len(jf_insertion_to_remove) + j_3p_to_remove > 0:
        rseq = rseq[ : -(len(jf_insertion_to_remove) + j_3p_to_remove)]
    line['indelfos'][iseq]['reversed_seq'] = rseq
    for indel in line['indelfos'][iseq]['indels']:
        indel['pos'] -= len(fv_insertion_to_remove) + v_5p_to_remove

# ----------------------------------------------------------------------------------------
def deal_with_indel_stuff(line, reset_indel_genes=False, debug=False):  # this function sucks, because it has to handle both the case where we're reconstucting the indel info from info in a file, and the case where we're checking what's already there
    if 'indelfos' in line and 'reversed_seq' not in line['indelfos'][0]:  # old-style files
        for iseq in range(len(line['unique_ids'])):
            reconstruct_indelfo_from_indel_list(line['indelfos'][iseq], line, iseq, debug=debug)
    elif 'has_shm_indels' in line:  # new-style files
        line['indelfos'] = [reconstruct_indelfo_from_gap_seqs_and_naive_seq(line['qr_gap_seqs'][iseq], line['gl_gap_seqs'][iseq], {r : line[r + '_gene'] for r in utils.regions}, line, iseq, debug=debug) for iseq in range(len(line['unique_ids']))]
        for key in ['has_shm_indels', 'qr_gap_seqs', 'gl_gap_seqs']:
            if key in line:
                del line[key]

    if reset_indel_genes:  # for when we get a new annotation (after reversing the indel), and it's got different genes
        reset_indelfos_for_new_genes(line)

    check_indelfo_consistency(line)

# ----------------------------------------------------------------------------------------
def reconstruct_indelfo_from_indel_list(indel_list, line, iseq, debug=False):  # old-style files
    if 'reversed_seq' in indel_list:  # handle super-old files
        print '%s encountered file with super old, unhandled indel format, proceeding, but indel info may be inconsistent' % (utils.color('red', 'error'))
        return

    line['indelfos'][iseq] = get_empty_indel()
    if len(indel_list) == 0:
        return

    ifo_positions = [ifo['pos'] for ifo in indel_list]
    if len(ifo_positions) != len(set(ifo_positions)):
        print '%s two indels at the same position, everything will be kinda messed up' % utils.color('red', 'error')
    ifos_by_pos = {ifo['pos'] : ifo for ifo in indel_list}
    qr_gap_seq, gl_gap_seq = [], []
    iqr, igl, iindel = 0, 0, 0
    if debug:
        print len(line['input_seqs'][iseq]), line['input_seqs'][iseq]
        print len(line['naive_seq']), line['naive_seq']
    while iqr < len(line['input_seqs'][iseq]):
        if debug:
            print '  %3d  %3d' % (iqr, igl),
        if iindel in ifos_by_pos:
            ifo = ifos_by_pos[iindel]
            if ifo['type'] == 'insertion':
                if ifo['seqstr'] != line['input_seqs'][iseq][iqr : iqr + ifo['len']]:
                    print '%s indel info seqstr doesn\'t match input seq str:' % utils.color('red', 'error')
                    utils.color_mutants(ifo['seqstr'], line['input_seqs'][iseq][iqr : iqr + ifo['len']], align=True, print_result=True, extra_str='        ')
                qr_gap_seq += ifo['seqstr'].split()
                gl_gap_seq += [ifo['len'] * utils.gap_chars[0]]
                if debug:
                    print '  %s    %s' % (ifo['seqstr'].split(), [ifo['len'] * utils.gap_chars[0]])
                iqr += ifo['len']
            else:
                if ifo['seqstr'] != line['naive_seq'][igl : igl + ifo['len']]:
                    print '%s indel info seqstr doesn\'t match naive seq str:' % utils.color('red', 'error')
                    utils.color_mutants(ifo['seqstr'], line['naive_seq'][igl : igl + ifo['len']], align=True, print_result=True, extra_str='        ')
                qr_gap_seq += [ifo['len'] * utils.gap_chars[0]]
                gl_gap_seq += ifo['seqstr'].split()
                if debug:
                    print '  %s    %s' % ([ifo['len'] * utils.gap_chars[0]], ifo['seqstr'].split())
                igl += ifo['len']
            del ifos_by_pos[iindel]
            iindel += ifo['len']
        else:
            qr_gap_seq += [line['input_seqs'][iseq][iqr]]
            gl_gap_seq += [line['naive_seq'][igl]]
            if debug:
                print '  %s    %s' % (line['input_seqs'][iseq][iqr], line['naive_seq'][igl])
            iqr += 1
            igl += 1
            iindel += 1

    line['indelfos'][iseq]['qr_gap_seq'] = ''.join(qr_gap_seq)
    line['indelfos'][iseq]['gl_gap_seq'] = ''.join(gl_gap_seq)
    line['indelfos'][iseq]['indels'] = indel_list
    line['indelfos'][iseq]['reversed_seq'] = line['indel_reversed_seqs'][iseq]
    line['indelfos'][iseq]['genes'] = {r : line[r + '_gene'] for r in utils.regions}  # TODO maybe don't include the gene and qrbounds info
    if debug:
        print '  reconstructed indelfo'
        print get_dbg_str(line['indelfos'][iseq])

# ----------------------------------------------------------------------------------------
def get_cigarstr_from_gap_seqs(qr_gap_seq, gl_gap_seq, debug=False):
    def gettype(ipos):
        qrb, glb = qr_gap_seq[ipos], gl_gap_seq[ipos]
        if qrb not in utils.gap_chars and glb not in utils.gap_chars:
            return 'M'
        elif glb in utils.gap_chars:
            return 'I'
        elif qrb in utils.gap_chars:
            return 'D'
        else:
            assert False  # the shouldn't both be gaps

    cigars = []
    assert len(gl_gap_seq) == len(qr_gap_seq)
    if debug:
        print '  reconstructing cigar'
        print '     qr  %3d  %s' % (len(qr_gap_seq), qr_gap_seq)
        print '     gl  %3d  %s' % (len(gl_gap_seq), gl_gap_seq)
    for ipos in range(len(qr_gap_seq)):
        if ipos == 0 or gettype(ipos) != gettype(ipos - 1):
            cigars.append([gettype(ipos), 0])
        cigars[-1][1] += 1

    if debug:
        print '   cigars: %s' % cigars

    # TODO fix this, probably uncomment it and make it a faster check
# # ----------------------------------------------------------------------------------------
#     cigar_len = sum([length for code, length in cigars])
#     if cigar_len != len(qr_gap_seq):
#         raise Exception('cigar length %d doesn\'t match qr gap seq length %d' % (cigar_len, seqtype, len(qr_gap_seq)))
#     if cigar_len != len(gl_gap_seq):
#         raise Exception('cigar length %d doesn\'t match gl gap seq length %d' % (cigar_len, seqtype, len(gl_gap_seq)))
#     # utils.color_mutants(line['input_seqs'][iseq], qr_gap_seq, align=True, print_result=True, ref_label='input  ', seq_label='qr gap ')
#     # print len(line['input_seqs'][iseq]), len(qr_gap_seq)
#     # assert len(line['input_seqs'][iseq]) == utils.non_gap_len(qr_gap_seq)
# # ----------------------------------------------------------------------------------------

    cigarstr = ''.join(['%d%s' % (l, c) for c, l in cigars])
    return cigarstr

# ----------------------------------------------------------------------------------------
def check_indelfo_consistency(line, debug=False):
    for iseq in range(len(line['unique_ids'])):
        check_single_sequence_indels(line, iseq, debug=debug)

# ----------------------------------------------------------------------------------------
def reset_indelfos_for_new_genes(line, debug=False):
    for iseq in range(len(line['unique_ids'])):
        reset_indelfo_for_new_genes(line, iseq, debug=debug)

# ----------------------------------------------------------------------------------------
def reset_indelfo_for_new_genes(line, iseq, debug=False):
    if not has_indels(line['indelfos'][iseq]):
        return
    new_cigarstr = get_cigarstr_from_gap_seqs(line['indelfos'][iseq]['qr_gap_seq'], line['indelfos'][iseq]['gl_gap_seq'], debug=debug)  # these gap seqs still correspond to the _old_ genes, but that's ok since they'll get replaced (we're just using them to figure out where the gaps go)
    new_indelfo = get_indelfo_from_cigar_and_line(new_cigarstr, line, iseq, debug=debug)
    for key in new_indelfo:  # shenanigans to keep waterer.info['indels'] the same dict as waterer.info[qname]['indelfos'][0]
        if line['indelfos'][iseq][key] != new_indelfo[key]:
            line['indelfos'][iseq][key] = new_indelfo[key]

# ----------------------------------------------------------------------------------------
def reconstruct_indelfo_from_gap_seqs_and_naive_seq(qr_gap_seq, gl_gap_seq, indel_genes, line, iseq, debug=False):  # either a <line> that doesn't already have <indelfos> in it (from a new-style file), or it does but we want to reconstruct the indelfos to make sure we get the same thing
    # NOTE passing gap seqs separately on purpose! don't use any that might be in <line>
    if utils.gap_len(qr_gap_seq) == 0 and utils.gap_len(gl_gap_seq) == 0:
        return get_empty_indel()

    # make a new cigar str using the gapped sequences, then combine that cigar str with info from <line> to make a new indelfo
    new_cigarstr = get_cigarstr_from_gap_seqs(qr_gap_seq, gl_gap_seq, debug=debug)
    new_indelfo = get_indelfo_from_cigar_and_line(new_cigarstr, line, iseq, debug=debug)
    return new_indelfo

# ----------------------------------------------------------------------------------------
def check_single_sequence_indels(line, iseq, print_on_err=True, debug=False):
    # debug = 2
    def check_single_ifo(old_ifo, new_ifo):
        if debug:
            print '  len %d  pos %d  seqstr %s' % (old_ifo['len'], old_ifo['pos'], old_ifo['seqstr']),
        if new_ifo != old_ifo:
            if debug:
                print '  %s' % utils.color('red', 'nope')
            new_seqstr, old_seqstr = utils.color_mutants(old_ifo['seqstr'], new_ifo['seqstr'], return_ref=True, align=True) #len(old_ifo['seqstr']) != len(new_ifo['seqstr']))
            if print_on_err:
                print '  pos %d --> %s    len %d --> %s    seqstr %s --> %s' % (old_ifo['pos'], utils.color(None if new_ifo['pos'] == old_ifo['pos'] else 'red', '%d' % new_ifo['pos']),
                                                                                old_ifo['len'], utils.color(None if new_ifo['len'] == old_ifo['len'] else 'red', '%d' % new_ifo['len']),
                                                                                old_seqstr, new_seqstr)
            return False
        else:
            if debug:
                print '  %s' % utils.color('green', 'ok')
            return True

    indelfo = line['indelfos'][iseq]
    if not has_indels(indelfo):
        return

    consistent = True

    new_indelfo = reconstruct_indelfo_from_gap_seqs_and_naive_seq(line['indelfos'][iseq]['qr_gap_seq'], line['indelfos'][iseq]['gl_gap_seq'], indelfo['genes'], line, iseq, debug=debug)

    if set(new_indelfo['genes']) != set(indelfo['genes']):
        if print_on_err:
            print '%s different indel regions before %s and after %s reconstruction' % (utils.color('red', 'error'), ' '.join((indelfo['genes'].keys())), ' '.join(new_indelfo['genes'].keys()))
        consistent = False
    else:
        for region in indelfo['genes']:
            if new_indelfo['genes'][region] != indelfo['genes'][region]:
                if print_on_err:
                    print '%s different indel genes before %s and after %s reconstruction' % (utils.color('red', 'error'), utils.color_gene(indelfo['genes'][region]), utils.color_gene(new_indelfo['genes'][region]))
                consistent = False

    if len(new_indelfo['indels']) != len(indelfo['indels']):
        if print_on_err:
            print '%s different number of indels before %d and after %d reconstruction' % (utils.color('red', 'error'), len(indelfo['indels']), len(new_indelfo['indels']))
        consistent = False

    old_indel_list, new_indel_list = copy.deepcopy(indelfo['indels']), copy.deepcopy(new_indelfo['indels'])
    old_positions, new_positions = [ifo['pos'] for ifo in old_indel_list], [ifo['pos'] for ifo in new_indel_list]
    if old_positions == new_positions:
        if debug:
            print '  same positions in old and new indelfos: %s' % ' '.join([str(p) for p in old_positions])
    elif set(new_positions) == set(old_positions):
        if debug:  # I think this'll only happen on old simulation files (ok, I can't really call them "old" yet since I haven't fixed it, but at some point I will, and then everybody's positions will then be sorted)
            print '  sorting both indel lists'
        old_indel_list = sorted(old_indel_list, key=lambda q: q['pos'])
        new_indel_list = sorted(new_indel_list, key=lambda q: q['pos'])
    else:
        consistent = False
        if print_on_err:
            print '  inconsistent position lists:\n  old  %s\n  new  %s' % (' '.join([str(p) for p in sorted(old_positions)]), ' '.join([str(p) for p in sorted(new_positions)]))

    if consistent:  # i.e. if nothing so far has been inconsistent
        for old_ifo, new_ifo in zip(old_indel_list, new_indel_list):
            consistent &= check_single_ifo(old_ifo, new_ifo)

    if not consistent:
        if print_on_err:
            print '%s inconsistent indel info for %s (see previous lines)' % (utils.color('red', 'error'), ':'.join(line['unique_ids']))
            print '       original:'
            print utils.pad_lines(get_dbg_str(indelfo), 8)
            print '       reconstructed:'
            print utils.pad_lines(get_dbg_str(new_indelfo), 8)

# ----------------------------------------------------------------------------------------
def combine_indels(regional_indelfos, full_qrseq, qrbounds, uid=None, debug=False):
    # debug = 2
    joint_indelfo = get_empty_indel()
    joint_indelfo['indels'] = []  # TODO probably add this stuff to get_empty_indel()
    joint_indelfo['genes'] = {}
    if 'd' not in qrbounds:  # arbitrarily give the d one base, and the j the rest of the sequence (I think they shouldn't affect anything as long as there's no d or j indels here)
        qrbounds['d'] = (qrbounds['v'][1], qrbounds['v'][1] + 1)
    if 'j' not in qrbounds:
        qrbounds['j'] = (qrbounds['d'][1], len(full_qrseq))
    if qrbounds['v'][1] > qrbounds['d'][0] or qrbounds['d'][1] > qrbounds['j'][0]:
        raise Exception('overlapping qr bounds (fix \'em before passing into here):  %s' % '   '.join([('%s %s' % (r, qrbounds[r])) for r in utils.regions]))

    if debug > 1:
        print 'combining %d indelfo%s from %s' % (len(regional_indelfos), utils.plural(len(regional_indelfos)), ' '.join([r for r in utils.regions if r in regional_indelfos]))
        print '  qrbounds:   %s' % '   '.join([('%s %s' % (r, qrbounds[r])) for r in utils.regions])
        print '     full qr %s' % full_qrseq
    qr_gap_seq, gl_gap_seq = [], []
    for region in utils.regions:
        ireg = utils.regions.index(region)
        if debug > 1:
            print '  %s' % region
        if region in regional_indelfos:
            rfo = regional_indelfos[region]
            assert has_indels(rfo)  # calling fcn needs to not add it if it doesn't have indels
            joint_indelfo['genes'][region] = rfo['genes'][region]
            if utils.non_gap_len(rfo['qr_gap_seq']) != qrbounds[region][1] - qrbounds[region][0]:  # should be fixed by overlapping boundary shifter
                raise Exception('%sqr_gap_seq non-gap length %d not the same as qrbound length %d in %s region indelfo' % ('%s: ' % uid if uid is not None else '', utils.non_gap_len(rfo['qr_gap_seq']), qrbounds[region][1] - qrbounds[region][0], region))
            qr_gap_seq += [rfo['qr_gap_seq']]
            gl_gap_seq += [rfo['gl_gap_seq']]

            reg_indel_list = copy.deepcopy(rfo['indels'])
            for i_prev_reg in range(0, ireg):  # loop over previous regions
                prev_reg = utils.regions[i_prev_reg]
                if prev_reg not in regional_indelfos:  # don't need to do anything if the previous region didn't have indels
                    continue
                prev_reg_gaps = utils.gap_len(regional_indelfos[prev_reg]['qr_gap_seq'])  # number of gaps in the previous region's qr gap seq 
                for ifo in reg_indel_list:
                    ifo['pos'] += prev_reg_gaps
                    if debug > 1:
                        print '    add %d to pos for gaps in %s' % (prev_reg_gaps, prev_reg)
            joint_indelfo['indels'] += reg_indel_list
        else:
            qr_gap_seq += [full_qrseq[qrbounds[region][0] : qrbounds[region][1]]]
            gl_gap_seq += [utils.ambiguous_bases[0] * (qrbounds[region][1] - qrbounds[region][0])]
        if debug > 1:
            print '    %s\n    %s' % (qr_gap_seq[-1].replace(utils.gap_chars[0], utils.color('red', utils.gap_chars[0])), gl_gap_seq[-1].replace(utils.gap_chars[0], utils.color('red', utils.gap_chars[0])))

        if ireg < len(utils.regions) - 1:
            next_reg = utils.regions[ireg + 1]
            assert region + next_reg in utils.boundaries
            qr_gap_seq += [full_qrseq[qrbounds[region][1] : qrbounds[next_reg][0]]]
            gl_gap_seq += [utils.ambiguous_bases[0] * (qrbounds[next_reg][0] - qrbounds[region][1])]
            if debug > 1:
                print '  %s%s' % (region, next_reg)
                print '    %s\n    %s' % (full_qrseq[qrbounds[region][1] : qrbounds[next_reg][0]], utils.ambiguous_bases[0] * (qrbounds[next_reg][0] - qrbounds[region][1]))

    if debug > 1:
        print 'combined gap seqs:'
        print '  qr %s' % '  '.join(qr_gap_seq)
        print '  gl %s' % '  '.join(gl_gap_seq)

    joint_indelfo['qr_gap_seq'] = ''.join(qr_gap_seq)
    joint_indelfo['gl_gap_seq'] = ''.join(gl_gap_seq)
    assert len(joint_indelfo['qr_gap_seq']) == len(joint_indelfo['gl_gap_seq'])
    joint_indelfo['reversed_seq'] = get_reversed_seq(joint_indelfo['qr_gap_seq'], joint_indelfo['gl_gap_seq'], full_qrseq[ : qrbounds['v'][0]], full_qrseq[qrbounds['j'][1] : ])
    # assert 'N' not in joint_indelfo['reversed_seq']  # this happens if there's Ns in the initial sequence

    joint_indelfo['qr_gap_seq'] = full_qrseq[ : qrbounds['v'][0]] + joint_indelfo['qr_gap_seq'] + full_qrseq[qrbounds['j'][1] : ]
    joint_indelfo['gl_gap_seq'] = utils.ambiguous_bases[0] * qrbounds['v'][0] + joint_indelfo['gl_gap_seq'] + utils.ambiguous_bases[0] * (len(full_qrseq) - qrbounds['j'][1])

    if debug:
        print 'combined'
        print get_dbg_str(joint_indelfo)

    return joint_indelfo
