#include "proxy.h"


int main(int argc, char const *argv[]){
	int 						mean_sample, opPerWorker, opPerWorkerResidue;
	double 						service_time;
	char const 					*filename;
	struct config        		*configuration;
	struct traceConfig		 	*traceData;
	struct worker 			    *arrayWorkers;
	float 						sth, sti, stc;


	if (argc < 2) {
		fprintf(stderr, "Usage: %s <config_file.json>\n", argv[0]);
		return 1;
	}

	filename = argv[1];


    configuration = read_config(filename);

    snprintf(agent_container_prefix, sizeof(agent_container_prefix), "%s_agent", configuration->agent_type);

    // Load dynamic service times from CSV files based on configuration
    load_service_times(configuration);

	print_interpolation_points();


    traceData = read_configTrace(configuration->traces_number, configuration->traces_fileName);

	// Print the trace data
	printf("Workers %d\n", configuration->workers);
	printf("Stages %d\n", configuration->stages_number);
	for (int i = 0; i < configuration->stages_number; i++) {
		printf("Stage %d\n", configuration->stages[i]);
	}
	printf("Compression algo %s\n", configuration->compression_algo);
	printf("Hashing algo %s\n", configuration->hashing_algo);
	printf("IDA algo %s\n", configuration->ida_algo);
	printf("Traces number %d\n", configuration->traces_number);
	printf("Traces file name %s\n", configuration->traces_fileName);
	
	

	opPerWorker = 0;
	opPerWorkerResidue = 0;


	makeContainers( configuration );
	traceGenerator ( traceData , configuration->traces_number ) ;
	arrayWorkers = assignation(configuration, traceData);


	// Start execution from the first configured stage; chaining will forward to subsequent stages
	if (configuration->stages_number > 0) {
		deployThread_stages(configuration, arrayWorkers, configuration->stages[0]);
		/* wait for processing to fully complete (timeout 60s) */
		wait_for_outstanding_zero(60);
	}

	/* shutdown NFR managers and print metrics */
	shutdown_and_report_metrics(configuration);

	for (int i = 0; i < configuration->workers ; ++i) {
		stc = 0;
		sth = 0;
		sti = 0;
		for (int j = 0; j < arrayWorkers[i].sizeWorker; ++j) {
			stc += arrayWorkers[i].trace[j].service_time_c;
			sth += arrayWorkers[i].trace[j].service_time_h;
			sti += arrayWorkers[i].trace[j].service_time_ida;
		}

		printf("Worker: %d Files: %d Compress: %f Hashing: %f Indexing: %f IDA: %f \n",
			  i, arrayWorkers[i].sizeWorker, stc, sth, arrayWorkers[i].service_time,sti);
	}

	/* Aggregate per-stage metrics across all workers */
	printf("\n=== Stage Metrics ===\n");
	for (int si = 0; si < configuration->stages_number; ++si) {
		int stageNum = configuration->stages[si];
		double total_time = 0.0;
		long processed_objects = 0;
		for (int w = 0; w < configuration->workers; ++w) {
			int objs = arrayWorkers[w].sizeWorker;
			if (objs <= 0) continue;
			switch (stageNum) {
				case 1: /* compress */
					for (int j = 0; j < objs; ++j) {
						if (arrayWorkers[w].trace[j].service_time_c > 0.0f) {
							total_time += arrayWorkers[w].trace[j].service_time_c;
							processed_objects++;
						}
					}
					break;
				case 2: /* hashing */
					for (int j = 0; j < objs; ++j) {
						if (arrayWorkers[w].trace[j].service_time_h > 0.0f) {
							total_time += arrayWorkers[w].trace[j].service_time_h;
							processed_objects++;
						}
					}
					break;
				case 3: /* indexing */
					for (int j = 0; j < objs; ++j) {
						if (arrayWorkers[w].trace[j].service_time_idx > 0.0f) {
							total_time += arrayWorkers[w].trace[j].service_time_idx;
							processed_objects++;
						}
					}
					break;
				case 4: /* IDA */
					for (int j = 0; j < objs; ++j) {
						if (arrayWorkers[w].trace[j].service_time_ida > 0.0f) {
							total_time += arrayWorkers[w].trace[j].service_time_ida;
							processed_objects++;
						}
					}
					break;
				case 5: /* upload / IO */
					for (int j = 0; j < objs; ++j) {
						if (arrayWorkers[w].trace[j].service_time_io > 0.0f) {
							total_time += arrayWorkers[w].trace[j].service_time_io;
							processed_objects++;
						}
					}
					break;
				default:
					break;
			}
		}
		double avg = (processed_objects > 0) ? (total_time / (double)processed_objects) : 0.0;
		printf("Stage %d: total_time=%f s processed_objects=%ld avg_per_object=%f s\n", stageNum, total_time, processed_objects, avg);
	}

	for (int i = 0; i < configuration->workers; ++i) {
		free(arrayWorkers[i].trace);
	}
	free(arrayWorkers);
	free(traceData);
	free(configuration->traces_fileName);
	free(configuration);

	return 0;
}



