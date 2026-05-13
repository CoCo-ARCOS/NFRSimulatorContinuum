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
#include "cJSON.h"



//STRUCTS DECLARATION
struct traces {
	char				*traceName;
	double				size;
	float				mean_interarrival;	
	float 				service_time_c;
	float 				service_time_h;
	float 			service_time_idx; /* indexing */
	float 			service_time_ida; /* IDA/reconstruct */
	float 				service_time_io;
	int	MUESTRAS ;
};

struct traceConfig {
	long long unsigned	MUESTRAS ;
	float				inter_arrival;
	long long unsigned  DISTRIBUTION ;
	float				mean ;
	float				stddev ;
	float				SIZE ;
	float				stddevS ;
	long long unsigned  Concurrency ;
};

/* Distributed continuum: machines and links */
#define MAX_MACHINES 32
#define MAX_LINKS 128
/* Number of tasks in each pipeline per stage */
#define INPUT_TASKS 3  /* uncompress, decrypt, verify_hash */
#define OUTPUT_TASKS 3 /* compress, encrypt, compute_hash */

struct machine_node {
	char name[64];
	int stages[10];
	int stages_number;
};

struct link_node {
	char from[64];
	char to[64];
	double b_net; /* bytes/sec */
	double latency_ms; /* optional */
	/* runtime metrics */
	double bytes_transferred;
	int transfers_count;
	double total_transfer_time; /* seconds */
};


struct worker {
	int id;
	int sizeWorker;
	long sizeStorage;
	int interarrive;
	int stage;
	int stage_owner; /* Stage this worker belongs to (1..5) */
	int machine_id; /* Assigned machine index, -1 if local/not set */
	char agent_type[16];   /**< "output" or "input" */
	struct traces *trace;
	float service_time;
	double b_fs; /* filesystem bandwidth bytes/sec for this worker */
};

/**
 * @brief Config structure.
 * 
 * Config structure stores data of the upload service configuration.
 */
struct config{
  int workers;              		/**< Number of workers.*/
  int traces_number;                /**< Numer of traces.*/
  char* traces_fileName;            /**< FileName of traces configurations.*/
  char agent_type[16];              /**< Type of agent pipeline: output or input.*/
  int stages[10];                   /**< Stages to execute.*/
  int stages_number;                /**< Number of stages.*/
  char compression_algo[32];        /**< Compression algorithm.*/
  char hashing_algo[32];            /**< Hashing algorithm.*/
  char ida_algo[32];                /**< IDA algorithm.*/
  int ida_k;                        /**< IDA k_datos.*/
  int ida_m;                        /**< IDA m_paridad.*/
	double b_fs;                      /**< Theoretical filesystem bandwidth (bytes/sec). Configured in MB/s and converted at startup. */
	struct machine_node machines[MAX_MACHINES]; /**< Optional distributed machines */
	int machines_number;
	struct link_node links[MAX_LINKS]; /**< Links between machines */
	int links_number;
};

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

void makeContainers( struct config *configuration ) ;
void makeAgents( int workers, const char *agent_type ) ;

extern char agent_container_prefix[32];

void traceGenerator ( struct traceConfig *traceData , int numberTraces ) ;

long fileSize( char *fname ) ;

struct worker * assignation( struct config *configuration ,  struct traceConfig *traceData ) ;

void deployThread_stages(struct config * configuration, struct worker *arrayWorkers, int stageNumber) ; 

void *sendWorkstage ( void *threadarg ) ;

void serviceTime ( struct worker * my_data) ;

void compress_time ( struct worker * my_data ) ;

void decompress_time ( struct worker * my_data ) ;

void hashing_time ( struct worker * my_data ) ;

void indexing_time ( struct worker * my_data ) ;

void IDA_time ( struct worker * my_data ) ;

void IDA_reconstruct_time ( struct worker * my_data ) ;

void upload_time ( struct worker * my_data ) ;

/* NFR manager/worker scaffolding */
struct nfr_job {
	struct worker *w;
};

struct nfr_manager {
	int stage; /* stage number this manager handles */
	int task_id; /* task index inside pipeline (0..N-1) */
	char task_name[32]; /* human-readable task name */
	pthread_t *threads; /* pool of worker threads */
	int num_threads;
	struct nfr_job *queue;
	int q_head;
	int q_tail;
	int q_count;
	int q_size;
	pthread_mutex_t lock;
	pthread_cond_t cond_nonempty;
	pthread_cond_t cond_nonfull;
	int stop;
	int is_input; /* 1=input pipeline, 0=output pipeline */
	/* runtime metrics */
	long jobs_processed;
	double total_processing_time; /* seconds */
};
int nfr_manager_init(struct nfr_manager *m, int stage, int task_id, const char *task_name, int num_threads, int q_size, int is_input);
int nfr_manager_enqueue(struct nfr_manager *m, struct worker *w);
void nfr_manager_shutdown(struct nfr_manager *m);
void shutdown_and_report_metrics(struct config *configuration);
int wait_for_managers_empty(struct config *configuration, int timeout_seconds);
int wait_for_outstanding_zero(int timeout_seconds);