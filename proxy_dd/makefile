CC = gcc
THREAD = -lpthread
LDLIBS = -lm

all: main

main: main.o proxy.o service_time.o cJSON.o
	$(CC) -g -o main main.o proxy.o service_time.o cJSON.o $(LDLIBS) $(THREAD)

proxy.o: proxy.c proxy.h cJSON.h
	$(CC) -g -c proxy.c -o proxy.o

service_time.o: service_time.c service_time.h
	$(CC) -g -c service_time.c -o service_time.o

cJSON.o: cJSON.c cJSON.h
	$(CC) -g -c cJSON.c -o cJSON.o

main.o: main.c proxy.h service_time.h
	$(CC) -g -c main.c -o main.o

clean:
	rm -f *.o main

remove_containers:
	-docker rm -f output_agent0 output_agent1 output_agent2 \
	            input_agent0  input_agent1  input_agent2 \
	            application_agent0 application_agent1 application_agent2 \
	            trace_generator 2>/dev/null || true

delete_trace_results:
	rm -f traces/* results/*

run_output: main
	./main config.json

run_input: main
	./main config_input.json
