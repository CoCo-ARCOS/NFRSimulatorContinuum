#include "proxy.h"


int main(int argc, char const *argv[]){
	int 						mean_sample, opPerWorker, opPerWorkerResidue;
	double 						service_time;
	char const 					*filename;
	struct config        		*configuration;
	struct traceConfig		 	*traceData;
	struct worker 			    *arrayWorkers;
	float 						sth, sti, stc;


    configuration = malloc(sizeof(struct config));
    configuration = read_config();

    //arrayWorkers = (struct worker*) malloc(configuration->traces_number * sizeof (struct worker)) ;
    traceData = malloc(sizeof(struct traceConfig) * configuration->traces_number);
    traceData = read_configTrace( configuration->traces_number , configuration->traces_fileName );
	

	opPerWorker = 0;
	opPerWorkerResidue = 0;


	makeContainers( configuration->workers  );
	traceGenerator ( traceData , configuration->traces_number ) ;
	arrayWorkers = ( struct worker* ) malloc ( configuration->workers * sizeof ( struct worker ));
	arrayWorkers = assignation( configuration ,  traceData ) ;


	//STAGE 1 : Compress stage
	deployThread_stages( configuration, arrayWorkers, 1) ;

	//STAGE 2 : Hashing stage
	deployThread_stages( configuration, arrayWorkers, 2) ;

	//STAGE 3 : Indexing stage
	deployThread_stages( configuration, arrayWorkers, 3) ;

	//STAGE 4 : Dispersal stage (IDA)
	deployThread_stages( configuration, arrayWorkers, 4) ;	

	//STAGE 5 : Upload
	//deployThread_stages( configuration, arrayWorkers, 5) ;
	for (int i = 0; i < configuration->workers ; ++i) {
		stc = 0;
		sth = 0;
		sti = 0;
		for (int j = 0; j < arrayWorkers[i].sizeWorker; ++j) {
			stc += arrayWorkers[i].trace[j].service_time_c;
			sth += arrayWorkers[i].trace[j].service_time_h;
			sti += arrayWorkers[i].trace[j].service_time_i;
		}

		printf("Worker: %d Files: %d Compress: %f Hashing: %f Indexing: %f IDA: %f \n",
			  i, arrayWorkers[i].sizeWorker, stc, sth, arrayWorkers[i].service_time,sti);
	}


	return 0;
}



