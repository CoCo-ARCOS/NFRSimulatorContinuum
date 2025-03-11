CC = gcc
THREAD = -lpthread
LDLIBS = -lm

all: main

main: main.o proxy.o service_time.o
	$(CC) -g -o main main.o proxy.o service_time.o $(LDLIBS) $(THREAD)

proxy.o: proxy.c proxy.h
	$(CC) -g -c proxy.c -o proxy.o

service_time.o: service_time.c service_time.h
	$(CC) -g -c service_time.c -o service_time.o

main.o: main.c proxy.h service_time.h
	$(CC) -g -c main.c -o main.o

clean:
	rm -f *.o main
