CC = gcc
THREAD = -lpthread
LDLIBS = -lm -ljson-c
CONTAINER_PLATFORM ?= docker

# Use Conda environment paths if active
ifdef CONDA_PREFIX
  CFLAGS += -I$(CONDA_PREFIX)/include -I$(CONDA_PREFIX)/include/json-c
  LDFLAGS += -L$(CONDA_PREFIX)/lib -Wl,-rpath,$(CONDA_PREFIX)/lib
endif

all: nfr_dag_sim

nfr_dag_sim: main.o proxy.o service_time.o cJSON.o power_models.o
	$(CC) $(CFLAGS) $(LDFLAGS) -g -o nfr_dag_sim main.o proxy.o service_time.o cJSON.o power_models.o $(LDLIBS) $(THREAD)

power_models.o: power_models.c power_models.h proxy.h
	$(CC) $(CFLAGS) -g -c power_models.c -o power_models.o

proxy.o: proxy.c proxy.h cJSON.h power_models.h
	$(CC) $(CFLAGS) -g -c proxy.c -o proxy.o

service_time.o: service_time.c service_time.h
	$(CC) $(CFLAGS) -g -c service_time.c -o service_time.o

cJSON.o: cJSON.c cJSON.h
	$(CC) $(CFLAGS) -g -c cJSON.c -o cJSON.o

main.o: main.c proxy.h service_time.h
	$(CC) $(CFLAGS) -g -c main.c -o main.o

clean:
	rm -f *.o nfr_dag_sim

remove_containers:
ifeq ($(CONTAINER_PLATFORM),docker)
	-docker rm -f output_agent0 output_agent1 output_agent2 \
	            input_agent0  input_agent1  input_agent2 \
	            application_agent0 application_agent1 application_agent2 \
	            trace_generator 2>/dev/null || true
else
	@echo "No long-running containers are created for $(CONTAINER_PLATFORM)."
endif

delete_trace_results:
	rm -f traces/* results/*

run_output: nfr_dag_sim
	./nfr_dag_sim config.json

run_input: nfr_dag_sim
	./nfr_dag_sim config_input.json
