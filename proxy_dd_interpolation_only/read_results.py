import sys
import os
from os.path import isfile, join

for trazas in [10,100,1000]:
    for workers in [1,3,6, 12]:
        directory = "new_results/%dt/%dw" % (trazas, workers)

        file_prototype = "w%d_stage%d.txt"
        results = []

        for i in range(workers):
            results.append(0)
        for i in range(workers): #workers
            results_worker = []
            for j in range(2,5): #stages
                #print file_prototype % (i, j)
                file_path = join(directory, file_prototype % (i, j))
                if os.path.isfile(file_path):
                    f = open(file_path, "r")
                    lines_o = f.readlines()
                    avg_delay_queue = 0
                    avg_number_queue = 0
                    avg_server_utilization = 0
                    avg_time_simulation = 0
                    avg_time_storage = 0
                    results_stage = []
                    for l in lines_o:
                        data = l.split("\t")
                        #print file_path, data
                        avg_delay_queue += float(data[0])
                        avg_number_queue += float(data[1])
                        avg_server_utilization += float(data[2])
                        avg_time_simulation += float(data[3])
                        break

                    results[i] += avg_time_simulation

        #for i in range(workers):
        print trazas, workers, max(results)
#         results_stage.append(avg_delay_queue/workers)
#         results_stage.append(avg_number_queue/workers)
#         results_stage.append(avg_server_utilization/workers)
#         results_stage.append(avg_time_simulation/workers)
#         results_stage.append(avg_time_storage/workers)
#         results_worker.append(results_stage)
#     results.append(results_worker)
# print results
#     #print l