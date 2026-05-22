CC=gcc -fcommon
THREAD=-lpthread 
LDLIBS='-lm'

all: main
main: main.c generator.o genericlib.o statlib.o
	$(CC) -g -o main main.c generator.o genericlib.o statlib.o  $(LDLIBS) $(THREAD)

clean: 
	rm -f *.o main
