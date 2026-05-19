import sys



workers = int(sys.argv[1])
sample = float(sys.argv[2])

input_file = "results_%d_%d.txt" % (workers, sample)
#print input_file
f= open(input_file,"r")

ommit_until =  workers + 5
todas_lineas = f.readlines()
lines = todas_lineas[ommit_until:]

avg_delay_queue = 0
avg_number_queue = 0
avg_server_utilization = 0
avg_time_simulation = 0
avg_time_storage = 0
trhoughput = 0
total_size = 0

for l in lines:
    data = l.split("\t")
    avg_delay_queue += float(data[0])
    avg_number_queue += float(data[1])
    avg_server_utilization += float(data[2])
    avg_time_simulation += float(data[3])

inicio = 2
fin = workers+inicio
lines = todas_lineas[inicio:fin]
storage_size = 0
for l in lines:
	data = l.split("\t")
	storage_size += float(data[1])
	total_size+=storage_size

avg_time_storage = storage_size/workers
avg_time_simulation/=workers
avg_number_queue/=workers
avg_server_utilization/=workers
avg_time_simulation/=workers
trhoughput = avg_time_storage/avg_time_simulation

print "Number of Workers:\t" , workers, "\n", \
      "Average delay Queque:\t" ,avg_delay_queue, "\n", \
      "Average number Queue:\t" ,avg_number_queue, "\n",\
      "Average server utilization:\t", avg_server_utilization, "\n", \
      "Average time Simulation:\t", avg_time_simulation, "\n", \
      "Average Storage in Nodes:\t", avg_time_storage, "\n", \
      "Trhoughput:\t", trhoughput ,"\n", \
      "Total Size:\t", total_size                 


f.close()