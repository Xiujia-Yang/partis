#!/usr/bin/env python
# has to be its own script, since ete3 requires its own god damn python version, installed in a separated directory
import itertools
import glob
import argparse
import copy
import random
import os
import tempfile
import subprocess
import sys
import ete3
import colored_traceback.always
from collections import OrderedDict

sys.path.insert(1, './python')
import utils
import glutils
sys.path.insert(1, './datascripts')
import heads

scolors = {
    'ok' : 'DarkSeaGreen',
    'missing' : '#d77c7c',  #'IndianRed',
    'spurious' : '#a44949',  #'IndianRed',
    'data' : 'LightSteelBlue',
    'both' : 'LightGrey',
}
metafos = heads.read_metadata('kate-qrs')
for ds in metafos:
    if 'LN1' in ds or 'LN2' in ds:
        scolors[ds] = '#85ad98'  # green
    elif 'LN4' in ds or 'LN3' in ds:
        scolors[ds] = '#94a3d1'  # blue
faces = {}
# faces = { 'missing' : ete3.AttrFace("name", fsize=30)}
#         # faces.add_face_to_node(N, node, 0, position="aligned")}
# faces = {'missing'  : ete3.CircleFace(10, 'white'),
#          'spurious' : ete3.CircleFace(10, 'black')}

def get_cmdfos(cmdstr, workdir, outfname):
    return [{'cmd_str' : cmdstr,
             'workdir' : workdir,
             'outfname' : outfname}]

# ----------------------------------------------------------------------------------------
def shorten_name(name):
    if name[:2] != 'IG':
        raise Exception('bad node name %s' % name)

    pv, sv, allele = utils.split_gene(name)
    if sv is not None:
        return '%s-%s*%s' % (pv, sv, allele)
    else:
        return '%s*%s' % (pv, allele)

# ----------------------------------------------------------------------------------------
def make_tree(all_genes, workdir, use_cache=False):
    aligned_fname = workdir + '/all-aligned.fa'
    raxml_label = 'xxx'
    raxml_output_fnames = ['%s/RAxML_%s.%s' % (workdir, fn, raxml_label) for fn in ['parsimonyTree', 'log', 'result', 'info', 'bestTree']]
    treefname = [fn for fn in raxml_output_fnames if 'result' in fn][0]
    if use_cache:  # don't re-run muxcle & raxml, just use the previous run's output tree file
        return treefname
    utils.prep_dir(workdir, wildlings=['*.' + raxml_label, os.path.basename(aligned_fname), 'out', 'err', os.path.basename(aligned_fname) + '.reduced'])

    # write and align an .fa with all alleles from any gl set
    with tempfile.NamedTemporaryFile() as tmpfile:
        for name, seq in all_genes.items():
            tmpfile.write('>%s\n%s\n' % (name, seq))
        tmpfile.flush()  # BEWARE if you forget this you are fucked
        cmdstr = '%s -in %s -out %s' % (args.muscle_path, tmpfile.name, aligned_fname)
        if args.debug:
            print '    %s %s' % (utils.color('red', 'run'), cmdstr)
        utils.run_cmds(get_cmdfos(cmdstr, workdir, aligned_fname), ignore_stderr=True)

    # get a tree for the aligned .fa
    cmdstr = '%s -mGTRCAT -n%s -s%s -p1 -w%s' % (args.raxml_path, raxml_label, aligned_fname, workdir)
    if args.debug:
        print '    %s %s' % (utils.color('red', 'run'), cmdstr)
    utils.run_cmds(get_cmdfos(cmdstr, workdir, treefname), ignore_stderr=True)

    os.remove(aligned_fname)  # rm muscle output
    for fn in [f for f in raxml_output_fnames if f != treefname]:  # rm all the raxml outputs except what the one file we really want
        os.remove(fn)

    return treefname

# ----------------------------------------------------------------------------------------
def getstatus(gl_sets, node):
    gene = node.name
    if not node.is_leaf():
        return 'internal'
    elif gene in gl_sets['sim'] and gene in gl_sets['inf']:
        return 'ok'
    elif gene in gl_sets['sim']:
        return 'missing'
    elif gene in gl_sets['inf']:
        return 'spurious'
    else:
        raise Exception('couldn\'t decide on status for node with name %s' % gene)

# ----------------------------------------------------------------------------------------
def getdatastatus(gl_sets, node, pair=False):
    gene = node.name

    if not pair:
        return 'leaf' if node.is_leaf() else 'internal'

    foundlist = [ds for ds in gl_sets if gene in gl_sets[ds]]

    if not node.is_leaf():
        return 'internal'
    elif len(foundlist) == len(gl_sets):
        return 'both'
    else:
        assert len(foundlist) == 1  # a.t.m <gl_sets> has to have length two, so...
        return foundlist[0]

# ----------------------------------------------------------------------------------------
def print_results(gl_sets):
    tmpfo = OrderedDict([['missing', set(gl_sets['sim']) - set(gl_sets['inf'])],
                         ['spurious', set(gl_sets['inf']) - set(gl_sets['sim'])],
                         ['ok', set(gl_sets['inf']) & set(gl_sets['sim'])]])  # ordered just to make printing easier
    for name, genes in tmpfo.items():
        genestr = ' '.join([utils.color_gene(g) for g in genes])
        if name == 'ok':
            print '       %2d %s %s' % (len(genes), name, genestr)
        else:
            print '    %-8s' % name,
            if len(genes) == 0:
                print ' %s' % utils.color('blue', 'none')
            else:
                print ' %2d    %s' % (len(genes), genestr)

# ----------------------------------------------------------------------------------------
def print_data_results(gl_sets):
    assert len(gl_sets) == 1  # would need to update
    for name, genes in gl_sets.items():
        print '    %9s %2d: %s' % (name, len(genes), ' '.join([utils.color_gene(g) for g in genes]))

# ----------------------------------------------------------------------------------------
def print_data_pair_results(gl_sets):
    assert len(gl_sets) == 2  # would need to update
    ds_1, ds_2 = gl_sets.keys()
    tmpfo = {ds_1 : set(gl_sets[ds_1]) - set(gl_sets[ds_2]),
             ds_2 : set(gl_sets[ds_2]) - set(gl_sets[ds_1]),
             'both' : set(gl_sets[ds_2]) & set(gl_sets[ds_1])}
    for name, genes in tmpfo.items():
        print '    %9s %2d: %s' % (name, len(genes), ' '.join([utils.color_gene(g) for g in genes]))

# ----------------------------------------------------------------------------------------
def get_gene_sets(glsfnames, glslabels, ref_label=None, classification_fcn=None, debug=False):
    glfos = {}
    for label, fname in zip(glslabels, glsfnames):
        gldir = os.path.dirname(fname).replace('/' + args.locus, '')
        glfos[label] = glutils.read_glfo(gldir, args.locus)  # this is gonna fail for tigger since you only have the .fa

    if ref_label is not None:
        for label in [l for l in glslabels if l != ref_label]:
            if debug:
                print '    syncronizing %s names to match %s' % (label, ref_label)
            glutils.synchronize_glfos(ref_glfo=glfos[ref_label], new_glfo=glfos[label], region=args.region, debug=debug)

    gl_sets = {label : {g : seq for g, seq in glfos[label]['seqs'][args.region].items()} for label in glfos}
    all_genes = {g : s for gls in gl_sets.values() for g, s in gls.items()}

    if classification_fcn is not None:
        all_primary_versions = set([classification_fcn(g) for g in all_genes])
        gl_sets = {pv : {label : {g : gl_sets[label][g] for g in gl_sets[label] if classification_fcn(g) == pv} for label in gl_sets} for pv in all_primary_versions}
        all_genes = {pv : {g : s for g, s in all_genes.items() if classification_fcn(g) == pv} for pv in all_primary_versions}

    return all_genes, gl_sets

# ----------------------------------------------------------------------------------------
def set_node_style(node, status, data=False, pair=False):
    linewidth = 2
    if data:
        if pair:
            if status != 'internal':
                node.img_style['bgcolor'] = scolors[status]
        else:
            node.img_style['bgcolor'] = scolors['data']
    elif status != 'internal':
        node.img_style['bgcolor'] = scolors[status]

    if status != 'internal' and glutils.is_novel(node.name):
        node.add_face(ete3.CircleFace(2.5, 'Gold'), column=0) #, position='aligned')

    node.img_style['hz_line_width'] = linewidth
    node.img_style['vt_line_width'] = linewidth

    if status in faces:
        node.add_face(copy.deepcopy(faces[status]), column=0, position='aligned')

# ----------------------------------------------------------------------------------------
def get_entirety_of_gene_family(root, family):
    # return set([leaf.name for leaf in node.get_tree_root() if utils.gene_family(leaf.name) == gene_family])
    return set([leaf.name for leaf in root if utils.gene_family(leaf.name) == family])

# ----------------------------------------------------------------------------------------
def set_distance_to_zero(node, debug=False):
    if node.is_root():
        return True
    if node.is_leaf():
        if len(get_entirety_of_gene_family(node.get_tree_root(), utils.gene_family(node.name))) == 1:  # if this is the *only* one from this family
            if debug:
                print '  family %s is of length 1 %s (set to zero)' % (utils.gene_family(node.name), node.name)
            return True
        else:
            return False

    descendents = set([leaf.name for leaf in node])
    gene_families = set([utils.gene_family(d) for d in descendents])
    if debug:
        print '  %s' % ' '.join([shorten_name(d) for d in descendents])
        print '      %s' % ' '.join(gene_families)
    if len(gene_families) == 0:
        raise Exception('zero length gene family set from %s' % ' '.join([leaf.name for leaf in node]))
    if len(gene_families) > 1:
        return True

    gene_family = list(gene_families)[0]
    entirety_of_gene_family = get_entirety_of_gene_family(node.get_tree_root(), gene_family)
    if debug:
        if len(entirety_of_gene_family - descendents) > 0:
            print '    missing %d / %d of family' % (len(entirety_of_gene_family - descendents), len(entirety_of_gene_family))
        elif len(descendents - entirety_of_gene_family) > 0:
            raise Exception('wtf should\'ve already returned')
        else:
            print '    setting to zero'
    return descendents == entirety_of_gene_family

# ----------------------------------------------------------------------------------------
def draw_tree(plotdir, plotname, treestr, gl_sets, all_genes, data=False, pair=False, arc_start=None, arc_span=None):
    etree = ete3.ClusterTree(treestr)
    node_names = set()  # make sure we get out all the genes we put in
    for node in etree.traverse():
        # print '%5.3f   %s' % (node.dist, node.name)
        if set_distance_to_zero(node):
            node.dist = 0.
        # if '+' in node.name and node.dist > 0.1:  # dammit, I should have named those differently...
        #     node.dist = 0.05
        if data:
            status = getdatastatus(gl_sets, node, pair=pair)
        else:
            status = getstatus(gl_sets, node)
        set_node_style(node, status, data=data, pair=pair)
        if node.is_leaf():
            node_names.add(node.name)
    if len(set(all_genes) - node_names) > 0:
        raise Exception('missing genes from final tree: %s' % ' '.join(node_names))

    if data:  # have to do it in a separate loop so it doesn't screw up the distance setting
        for node in [n for n in etree.traverse() if n.is_leaf()]:  # yeah I'm sure there's a fcn for that
            node.name = shorten_name(node.name)

    tstyle = ete3.TreeStyle()
    if not data:
        tstyle.show_leaf_name = False
    tstyle.mode = 'c'
    tstyle.show_scale = False
    if arc_start is not None:
        tstyle.arc_start = arc_start
    if arc_span is not None:
        tstyle.arc_span = arc_span
    etree.render(plotdir + '/' + plotname + '.svg', h=750, tree_style=tstyle)

# ----------------------------------------------------------------------------------------
def plot_gls_gen_tree(args, plotdir, plotname, glsfnames, glslabels, leg_title=None, title=None):
    assert glslabels == ['sim', 'inf']  # otherwise stuff needs to be updated

    all_genes, gl_sets = get_gene_sets(glsfnames, glslabels, ref_label='sim')
    print_results(gl_sets)

    treefname = make_tree(all_genes, plotdir + '/workdir', use_cache=args.use_cache)
    with open(treefname) as treefile:
        treestr = treefile.read().strip()
    draw_tree(plotdir, plotname, treestr, gl_sets, all_genes)

# ----------------------------------------------------------------------------------------
def plot_data_tree(args, plotdir, plotname, glsfnames, glslabels, leg_title=None, title=None):
    all_genes, gl_sets = get_gene_sets(glsfnames, glslabels)
    print_data_results(gl_sets)

    treefname = make_tree(all_genes, plotdir + '/workdir', use_cache=args.use_cache)
    with open(treefname) as treefile:
        treestr = treefile.read().strip()

    draw_tree(plotdir, plotname, treestr, gl_sets, all_genes, data=True, pair=False)

# ----------------------------------------------------------------------------------------
def plot_data_pair_tree(args, plotdir, plotname, glsfnames, glslabels, leg_title=None, title=None):
    all_genes, gl_sets = get_gene_sets(glsfnames, glslabels, ref_label=glslabels[0])
    print_data_pair_results(gl_sets)

    treefname = make_tree(all_genes, plotdir + '/workdir', use_cache=args.use_cache)
    with open(treefname) as treefile:
        treestr = treefile.read().strip()

    etree = ete3.ClusterTree(treestr)
    node_names = set()  # make sure we get out all the genes we put in
    for node in etree.traverse():
        node.dist = 1
        status = getdatastatus(gl_sets, node, pair=True)
        set_node_style(node, status, data=True, pair=True)
        if node.is_leaf():
            node_names.add(node.name)
    if len(set(all_genes) - node_names) > 0:
        raise Exception('missing genes from final tree: %s' % ' '.join(set(all_genes) - node_names))

    tstyle = ete3.TreeStyle()
    tstyle.show_leaf_name = True
    tstyle.mode = 'c'
    tstyle.show_scale = False
    etree.render(plotdir + '/' + plotname + '.svg', h=750, tree_style=tstyle)

# ----------------------------------------------------------------------------------------
parser = argparse.ArgumentParser()
parser.add_argument('--plotdir', required=True)
parser.add_argument('--plotname', required=True)
parser.add_argument('--glsfnames', required=True)
parser.add_argument('--glslabels', required=True)
parser.add_argument('--locus', required=True)
parser.add_argument('--use-cache', action='store_true')
parser.add_argument('--debug', action='store_true')
parser.add_argument('--title')
parser.add_argument('--region', default='v')
parser.add_argument('--muscle-path', default='./packages/muscle/muscle3.8.31_i86linux64')
parser.add_argument('--raxml-path', default=glob.glob('./packages/standard-RAxML/raxmlHPC-*')[0])

args = parser.parse_args()
args.glsfnames = utils.get_arg_list(args.glsfnames)
args.glslabels = utils.get_arg_list(args.glslabels)
if not os.path.exists(args.muscle_path):
    raise Exception('muscle path %s does not exist' % args.muscle_path)
if not os.path.exists(args.raxml_path):
    raise Exception('raxml path %s does not exist' % args.raxml_path)

assert len(args.glslabels) == len(set(args.glslabels))  # no duplicates

if args.glslabels == ['sim', 'inf']:
    plot_gls_gen_tree(args, args.plotdir, args.plotname, args.glsfnames, args.glslabels, title=args.title)
elif len(args.glsfnames) == 1:
    plot_data_tree(args, args.plotdir, args.plotname, args.glsfnames, args.glslabels, title=args.title)
else:
    plot_data_pair_tree(args, args.plotdir, args.plotname, args.glsfnames, args.glslabels, title=args.title)