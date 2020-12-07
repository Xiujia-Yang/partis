import sys
import time
import os
import scipy
import numpy
import collections

import utils

mdir = "~/work/partis/datascripts/meta/goo-dengue-10x"
barcodefname = 'barcodes.txt'
pcafname = 'pca.txt'
umapfname = 'umap.txt'
clusterfname = 'clusters.txt'

# ----------------------------------------------------------------------------------------
def gmarkfname(iclust):
    return 'markers-cluster-%d.txt' % iclust  # NOTE R indexing, starts from 1

# ----------------------------------------------------------------------------------------
def install():
    rcmds = ['install.packages("BiocManager", repos="http://cran.rstudio.com/"))',
             'BiocManager::install(c("scRNAseq", "scater", "scran", "uwot", "DropletUtils"), dependencies=TRUE)']  # "TENxPBMCData"
    workdir = utils.choose_random_subdir('/tmp/%s' % os.getenv('USER'))
    os.makedirs(workdir)
    utils.run_r(rcmds, workdir)
    os.rmdir(workdir)
# install()
# sys.exit()

# ----------------------------------------------------------------------------------------
def loadcmd(lib):
    return 'library(%s, warn.conflicts=F, quietly=T)' % lib

# ----------------------------------------------------------------------------------------
def read_gex(outdir):
    with open('%s/%s' % (outdir, pcafname)) as pfile:
        pca_comps = None  # names for each pca component (like PC3)
        rotation_vals = collections.OrderedDict()  # relationship between pca and gene names
        for il, line in enumerate(pfile):
            if il == 0:
                pca_comps = line.strip().split()
                for ipc, pc in enumerate(pca_comps):
                    assert pc[:2] == 'PC'
                    assert int(pc[2:]) == ipc + 1
                print '    read %d pca component headers' % len(pca_comps)
                continue
            lstrs = line.strip().split()
            gene = lstrs.pop(0)
            assert len(lstrs) == len(pca_comps)
            rotation_vals[gene] = [float(vstr) for vstr in lstrs]
    for gene, vals in rotation_vals.items():
        print gene, vals
    sys.exit()

# ----------------------------------------------------------------------------------------
def run_gex(feature_matrix_fname, outdir, make_plots=True, max_pca_components=25, n_top_genes=10):
    rcmds = [loadcmd(l) for l in ['DropletUtils', 'scater', 'scran', 'pheatmap']]
    rcmds += [
        'options(width=1000)',
        'sce <- read10xCounts("%s")' % feature_matrix_fname,
        'rownames(sce) <- uniquifyFeatureNames(rowData(sce)$ID, rowData(sce)$Symbol)',
        # quality control
        'is.mito <- grepl("^MT-", rownames(sce))',  # figure out which genes are mitochondrial
        'qcstats <- perCellQCMetrics(sce, subsets=list(Mito=is.mito))',
        'filtered <- quickPerCellQC(qcstats, percent_subsets="subsets_Mito_percent")',  # identifies + removes outliers (in several qc metrics)
        'sce <- sce[, !filtered$discard]',
        # normalization
        'sce <- logNormCounts(sce)',
        # feature selection
        # 'fabio.pb.genes <- read.csv("%s/plasmablast_markers.tsv", sep="\t", header=T)$GeneName' % , mdir # $name  # genes from fabio (200 most discriminatory between plasmablast + naive B cell):
        'waick.genes <- read.csv("%s/waickman-markers.csv", header=T)$gene' % mdir,  # 10 most up'd genes for naive, memory, pb, and prepb (40 total)
        'genelist <- waick.genes',  # fabio.pb.genes
        'print(sprintf("  using %d genes: %s", length(genelist), paste(genelist, collapse=" ")))',
        'gene.bools <- rowData(sce)$Symbol %in% genelist',  # $ID
        # dimensionality reduction
        'set.seed(1)',
        'n.comp <- min(%d, as.integer(length(genelist)/2))' % max_pca_components,
        'print(sprintf("running pca with %d components", n.comp))',
        'sce <- runPCA(sce, ncomponents=n.comp, subset_row=gene.bools)',
        'sce <- runUMAP(sce, dimred="PCA", external_neighbors=TRUE)',  # uses pca results from previous step TODO test variety of N neighbors and min_dist values
        # clustering
        'g <- buildSNNGraph(sce, use.dimred="PCA")',
        'colLabels(sce) <- factor(igraph::cluster_louvain(g)$membership)',
        # write output files (more written below)
        'capture.output(colData(sce)$Barcode, file="%s/%s")' % (outdir, barcodefname),
        'capture.output(attr(reducedDims(sce)$PCA, "rotation"), file="%s/%s")' % (outdir, pcafname),  # pca to gene name rotation
        # (reducedDim(sce, "PCA")[,]  # a table of the pca values for each cell
        'capture.output(reducedDim(sce, "UMAP")[,], file="%s/%s")' % (outdir, umapfname),
        'capture.output(colLabels(sce), file="%s/%s")' % (outdir, clusterfname),
    ]
    if make_plots:
        rcmds += [
            ## pdf(sprintf("%s/clusters.pdf", outdir))
            'png("%s/clusters.png")' % outdir,
            'plotUMAP(sce, colour_by="label")',
            'dev.off()',
        ]
    # find marker genes
    rcmds += [
        'markers <- findMarkers(sce)',  # <markers>: list of data frames for each cluster NOTE this uses *all* the genes, and i can't figure out a way to tell it not to
        'n.genes <- %d' % n_top_genes,
        'print(sprintf("  top %d genes for each cluster (total size %d)", n.genes, length(sce$label)))',
        'for(ich in seq(length(markers))) {'  # look at genes that distinguish cluster ich from all other clusters
        '    print(sprintf("   cluster %2d  size %4d  frac %.2f", ich, sum(sce$label==ich), sum(sce$label==ich) / length(sce$label)))',
        '    interesting <- markers[[ich]]',
        '    capture.output(interesting[1:n.genes,], file=sprintf("%s/markers-cluster-%%d.txt", ich))' % outdir,
        '    best.set <- interesting[interesting$Top <= n.genes,]',  # look at the top N genes from each pairwise comparison
        '    logFCs <- getMarkerEffects(best.set)',
    ]
    if make_plots:
        rcmds += [
            '    png(sprintf("%s/heatmap-%%d.png", ich))' % outdir,
            '    pheatmap(logFCs, breaks=seq(-5, 5, length.out=101))',
            '    dev.off()',
        ]
    rcmds += [
        '}',
    ]

    workdir = utils.choose_random_subdir('/tmp/%s' % os.getenv('USER'))
    os.makedirs(workdir)
    utils.run_r(rcmds, workdir)
    os.rmdir(workdir)
