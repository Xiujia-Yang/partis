#!/usr/bin/env python
from subprocess import check_call, Popen
import sys
sys.path.insert(1, './python')
import os
import csv

from clusterpath import ClusterPath
import utils

def run_cluster(cl, iclust=None):
    cmd = 'srun ./bin/run-driver.py --label ' + label + ' --action run-viterbi --is-data --datafname VRC01_heavy_chains-dealigned.fasta'  # --simfname ' + os.path.dirname(infname) + '/simu-foo-bar.csv'
    cmd += ' --outfname _tmp/chaim/' + str(iclust) + '.csv'
    extras = ['--n-sets', len(cl), '--queries', ':'.join(cl), '--debug', 0, '--sw-debug', 0, '--n-procs', 1, '--n-best-events', 1]
    cmd += utils.get_extra_str(extras)
    Popen(cmd.split())

label = 'chaim-test'
# infname = '/fh/fast/matsen_e/dralph/work/partis-dev/_output/' + label + '/partitions.csv'
infname = 'chaim-for-erick.csv'

cp = ClusterPath(-1)
cp.readfile(infname)

print '---> annotations for clusters in best partition:'
# print cp.i_best
# for ip in range(len(cp.partitions)):
#     print ip, len(cp.partitions[ip])
# sys.exit()

iclust = 0
for cluster in cp.partitions[355]:
    run_cluster(cluster, iclust)
    iclust += 1
    # if iclust > 3:
    #     break

sys.exit()

for ipart in range(cp.i_best, cp.i_best + 10):
    if ipart >= len(cp.partitions):
        break
    print '---> annotation for intersection of partitions %d and %d:' % (ipart - 1, ipart)
    cp.print_partition(ipart - 1)
    cp.print_partition(ipart)

    parents = cp.get_parent_clusters(ipart)
    if parents is None:
        print '   skipping synthetic rewind step'
        continue
    run_cluster(parents[0] + parents[1])
