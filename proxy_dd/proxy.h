#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <time.h>
#include <pthread.h>
#include <errno.h>
#include "service_time.h"




//STRUCTS DECLARATION
struct traces {
	char				*traceName;
	long				size;
	float				mean_interarrival;	
	float 				service_time_c;
	float 				service_time_h;
	float 				service_time_i;
	long long unsigned	MUESTRAS ;
};

struct traceConfig {
	long long unsigned	MUESTRAS ;
	float				inter_arrival;
	long long unsigned  DISTRIBUTION ;
	float				mean ;
	float				stddev ;
	float				TEMPERATURE ;
	float				stddevT ;
	float				PRESION ;
	float				stddevP ;
	float				HUMEDITY ;
	float				stddevH ;
	float				RADSOLAR ;
	float				stddevR ;
	long long unsigned  Concurrency ;
};


struct worker {
	int id;
	int sizeWorker;
	int sizeStorage;
	int interarrive;
	int stage;
	struct traces *trace;
	float service_time;
};

/**
 * @brief Config structure.
 * 
 * Config structure stores data of the upload service configuration.
 */
struct config{
  int workers;              		/**< Number of workers.*/
  int traces_number;                /**< Numer of traces.*/
  char* traces_fileName;            /**< FileName of traces configurations.*/};

/**
 * @brief Function returns error in the case to occur.
 * @param s Char string that contains the error.
 * @return Return the error. 
 */
void error(const char *s);

struct config *read_config() ;

struct traceConfig *read_configTrace( int numberTrace, char * fileName) ;

void execute_command ( char * command);

void makeTraceGenerator ( ) ;

void makeContainers( int workers ) ;

void traceGenerator ( struct traceConfig *traceData , int numberTraces ) ;

long fileSize( char *fname ) ;

struct worker * assignation( struct config *configuration ,  struct traceConfig *traceData ) ;

void deployThread_stages(struct config * configuration, struct worker *arrayWorkers, int stageNumber) ; 

void *sendWorkstage ( void *threadarg ) ;

void serviceTime ( struct worker * my_data) ;

void compress_time ( struct worker * my_data ) ;

void hashing_time ( struct worker * my_data ) ;

void indexing_time ( struct worker * my_data ) ;

void IDA_time ( struct worker * my_data ) ;

void upload_time ( struct worker * my_data ) ;