import sys

traces = int(sys.argv[1])
muestras = int(sys.argv[2])
arrival = float(sys.argv[3])
workers = int(sys.argv[4])

base = "%d %f %d %d %f %d %f %d %d %d %f %d %f %d\n"


res = ""

for i in range(traces):
    res += base % (muestras, arrival, 3, 15, 0.6, 30, 0.5, 1024, 10, 40, 2.24, 100, 2.485, 1)

f = open("traces.cfg", "w")
f.write(res)
f.close()


config = "workers:%d\ntraces_number:%d\ntraces_fileName:traces.cfg"

f = open("config.cfg", "w")
f.write(config % (workers, traces))
f.close()
