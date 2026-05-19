void otra () {
	
	int 						workers, mean_sample, opPerWorker, opPerWorkerResidue;
	double 						service_time;
	char const 					* filename;
	struct config        		*configuration;

    configuration = malloc(sizeof(struct config));
    configuration = read_config();
	opPerWorker = 0;
	opPerWorkerResidue = 0;

	filename = argv[1];
	workers = atoi(argv[2]);
	service_time = atol(argv[3]);
	mean_sample=atoi(argv[4]);

	//struct wo

	char **lines=malloc(sizeof (char*) * mean_sample);
	struct worker *arrayWorkers = (struct worker*) malloc(workers* sizeof (struct worker));

	char filenameOutput[200] =  "results";
	char aux1[200];

	sprintf(aux1, "_%d_%d.txt",workers, mean_sample);
	strcat(filenameOutput,aux1);
	printf("%s",filenameOutput);
	f = fopen(filenameOutput, "w+");

	if (f == NULL)
	{
		printf("Error opening file!\n");
		exit(1);
	}

	int count=0;

	//Read File
	FILE * fp;
	    char * line = NULL;
	    size_t len = 0;
	    ssize_t read;

	    fp = fopen(filename, "r");
	    if (fp == NULL)
	        exit(EXIT_FAILURE);

	   	if ( fp != NULL )	{
	      char line [ 128 ]; /* or other suitable maximum line size */

	      while ( fgets ( line, sizeof line, fp ) != NULL ) /* read a line */ {
	        	//fputs ( line, stdout ); /* write the line */
	        	lines[count] = (char*) malloc(sizeof(char)*128);
	    		strcpy(lines[count], line);
	    		count++;
	      }
	      fclose ( fp );
	   	} else {
	      perror ( filename ); /* why didn't the file open? */
	   	}
	   	//Fin Read File
	   	fprintf(f,"\n");
	    fprintf(f,"idWorker\tsizeStorage\tinterarrive\tRead\tWrite\tnumberOperations\n");
	    
	    int contar[workers];

	    for (int i = 0; i < workers; ++i){
	  	 	arrayWorkers[i].v=(struct dataTrace*) malloc(count * sizeof (struct dataTrace));
	  	 	contar[i] = 0;
	  	 	arrayWorkers[i].id=i;
			arrayWorkers[i].sizeStorage=0;
			arrayWorkers[i].interarrive=0;
			arrayWorkers[i].read=0;
			arrayWorkers[i].write=0;

	    }
	   	int c=0;
	   	int position = 0;
	    for (int i = 0; i < count; ++i){
	    	position = i%workers;
	    	
			struct dataTrace *tracita;
				char *token = strtok(lines[i]," ");
			    if(token != NULL){
			        while(token != NULL){
			            // Sólo en la primera pasamos la cadena; en las siguientes pasamos NULL
			            if (c==1||c==3){
			            	c++;
			            }else{
			            	if (c==0){
			            		arrayWorkers[position].v[contar[position]].interarrive=atoi(token);
			            	//printf("%d\n", arrayWorkers[position].v[contar[position]].interarrive);
			            		c++;
			            	}
			            	if (c==2){
			            		arrayWorkers[position].v[contar[position]].type=token;
			            		
			            		c++;
			            		if (*arrayWorkers[position].v[contar[position]].type=='r'){
			            			arrayWorkers[position].read+=1;
			            		}else{
			            			arrayWorkers[position].write+=1;
			            		}
			            	}
			            	if (c==4){
			            		arrayWorkers[position].v[contar[position]].portion=atoi(token);
			            		//printf("%i \n", arrayWorkers[i].v[j].portion);
			           		 	arrayWorkers[position].sizeStorage+=arrayWorkers[position].v[contar[position]].portion;
			            		c=0;
			            	}
			            }

			           	
			            token = strtok(NULL, " ");
			        }
			        
					contar[position]++;

			    }
	    }

	    
	    for (int i = 0; i < workers; ++i){
	    	int interarrivo_temp=0;
	    	for (int j = contar[i]-1; j > 0; --j){
	    		interarrivo_temp += arrayWorkers[i].v[j].interarrive-arrayWorkers[i].v[(j-1)].interarrive;
	    		//printf("%d\n", arrayWorkers[i].v[j].interarrive);
	    	}
			arrayWorkers[i].interarrive=interarrivo_temp/contar[i];
			fprintf(f,"%i\t%i\t%i\t%i\t%i\t%i\n", arrayWorkers[i].id, arrayWorkers[i].sizeStorage, arrayWorkers[i].interarrive,arrayWorkers[i].read, arrayWorkers[i].write,contar[i]);

	    }
			 	

	fprintf(f,"\n\nAverageDelayQueue\tAverageNumberQueue\tServerUtilization\tTimeSimulation\n");
	    for (int i = 0; i < workers; ++i){
	    	int numberOperations=arrayWorkers[i].read+arrayWorkers[i].write;
	    	char command[100] =  "docker run --rm single:worker ./single";
			char mean_intearrive[20];
			char mean_serviceTime[20];
			char curstomers[20];
	    	sprintf(mean_intearrive, " %d ",arrayWorkers[i].interarrive);
	    	strcat(command,mean_intearrive);

	    	sprintf(mean_serviceTime, " %f ",service_time);
	    	strcat(command,mean_serviceTime);

	    	sprintf(curstomers, " %d ",numberOperations);
	    	strcat(command,curstomers);


	    	//printf("%s\n", command);
	    	FILE *fp;
			char path[1035];

			fp = popen(command, "r");
			if (fp == NULL) {
			printf("Failed to run command\n" );
			exit(1);
			}

			// Read the output a line at a time - output it. 
			while (fgets(path, sizeof(path)-1, fp) != NULL) {
				fprintf(f,"%s", path);
			}

			pclose(fp);

		    }
}