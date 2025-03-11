#include "proxy.h"

/**
 * @brief Function returns error in the case to occur.
 */
void error(const char *s){
  perror(s);   //< perror() returns the S string and the error that found in errno.
  exit(EXIT_FAILURE);
}


/**
 * @brief Function that read the conbash command.sh rsync -czarvh datos/sort_data dsanchez@148.247.201.222:/home/dsanchezfig file.
 */
struct config *read_config() {
    FILE                  *file;
    char                  *file_name, *token, *delimitador, line[500], key[200], value[400];
    int                   linenum, cont;
    struct config         *configuration;

    configuration = malloc(sizeof(struct config));
    configuration->traces_fileName = malloc((255)*sizeof(char));
    token = malloc((255)*sizeof(char));
    delimitador = malloc((255)*sizeof(char));

    file_name = "config.cfg";
    linenum = 0;
    file = fopen ( file_name , "r" ); //< Read file

    while ( fgets( line , 256 , file ) != NULL ) {
        linenum++;
        if ( line[0] == '#') continue;

        delimitador = ":";
        token = strtok ( line , delimitador );
        cont = 0;

        if ( token != NULL ) {
            while ( token != NULL ) {
                if ( cont == 0 ) { //< Only in the first string, in the next string assigned NULL.
                  strcpy ( key , token );
                  token = strtok ( NULL, delimitador );
                } else {
                  strcpy ( value , token );
                  token = strtok ( NULL , delimitador );
                }
                cont++;
            }
        }

        if (strcmp(key,"workers")==0)
          configuration->workers = atoi(value);
        else if (strcmp(key,"traces_number")==0)
          configuration->traces_number = atoi(value);
        else if (strcmp(key,"traces_fileName")==0){
          strcpy(configuration->traces_fileName,value);
          configuration->traces_fileName[strcspn(configuration->traces_fileName,"\n")] = 0;
        }
    }
    return configuration;
}


struct traceConfig *read_configTrace( int numberTrace, char * fileName) {
    FILE                  	 *file;
    char                 	 *token, *delimitador, line[500], key[200], value[400];
    int                   	 linenum, cont , i;
    struct traceConfig		 *traceData;

    traceData =   malloc(sizeof(struct traceConfig) * numberTrace);
    token = malloc((255)*sizeof(char));
    delimitador = malloc((255)*sizeof(char));

    linenum = 0;
    file = fopen ( fileName , "r" ); //< Read file
    cont = 0;
    i = 1;

   while ( fgets( line , 256 , file ) != NULL ) {
        linenum++;
        if ( line[0] == '#') continue;

        delimitador = " ";
        token = strtok ( line , delimitador );
     	

        if ( token != NULL ) {
            while ( token != NULL ) {
            	switch ( i ) {

            		case 1:
            			strcpy ( value , token );
						traceData[cont].MUESTRAS = atoi(value);
            			break;
            		case 2:
            			strcpy ( value , token );
						traceData[cont].inter_arrival = atof(value);
            			break;
            		case 3:
            			strcpy ( value , token );
						traceData[cont].DISTRIBUTION = atoi(value);
            			break;
            		case 4:
            			strcpy ( value , token );
						traceData[cont].mean = atof(value);
            			break;
            		case 5:
            			strcpy ( value , token );
						traceData[cont].stddev = atof(value);
            			break;
            		case 6:
            			strcpy ( value , token );
						traceData[cont].TEMPERATURE = atof(value);
            			break;
            		case 7:
            			strcpy ( value , token );
						traceData[cont].stddevT = atof(value);
            			break;
            		case 8:
            			strcpy ( value , token );
						traceData[cont].PRESION = atof(value);
            			break;
            		case 9:
            			strcpy ( value , token );
						traceData[cont].stddevP = atof(value);
            			break;
            		case 10:
            			strcpy ( value , token );
						traceData[cont].HUMEDITY = atof(value);
            			break;
            		case 11:
            			strcpy ( value , token );
						traceData[cont].stddevH = atol(value);
            			break;
            		case 12:
            			strcpy ( value , token );
						traceData[cont].RADSOLAR = atof(value);
            			break;
            		case 13:
            			strcpy ( value , token );
						traceData[cont].stddevR = atof(value);
            			break;
            		case 14:
            			strcpy ( value , token );
						traceData[cont].Concurrency = atoi(value);
            			break;
            	}
				
				token = strtok ( NULL , delimitador );

                i++;

                if ( i > 14 ) {
                	i = 1;
                }
            }
        }
        cont ++;
    }
    return traceData;
}

void makeTraceGenerator ( ) {
	char                *command, *pwd;
    int                 size;

    pwd = getenv("PWD");
    size = 100 * 2 + strlen(pwd) * 2 + 4 + 200;
    command = malloc((size)*sizeof(char));
    
    strcpy(command, ""); //< Variable inizialitation
    sprintf(
      command,
      "docker run -i -d --name trace_generator -v \'%s/traces/\':\'%s\' trace:generator",
      pwd , pwd
    );

    execute_command ( command );	

    free ( command ) ;
}

void makeSingle ( int workers) {
    char                *command, *pwd;
    int                  i;

    pwd = getenv("PWD");
    

    for ( i = 0; i < workers ; ++i ){
        command = malloc(200+50+strlen(pwd)*3*sizeof(char));
        strcpy(command, "");
        sprintf(
                  command,
                  "docker run -i -d --name single%d -v \'%s/traces/\':\'%s\' single:queue",
                  i, pwd , pwd
        );

        execute_command ( command );    
        free ( command ) ;
    }
    
}


void makeContainers(int workers) {
 	makeTraceGenerator ( ) ;
    makeSingle ( workers ) ;
}

void traceGenerator ( struct traceConfig *traceData , int numberTraces ) {
	char 			*fileName, *baseName,  *command, *baseCommand , *pwd ;
	int				i;


	pwd = getenv("PWD");
	baseName = malloc( sizeof ( char ) * 20 );
	baseCommand = malloc( 300  * sizeof ( char ) );

	baseName = "trace%lld.txt";
	baseCommand = "docker exec trace_generator ./main %lld %f %lld %f %f %f %f %f %f %f %f %f %f %lld > \'%s/traces/%s\'";


	for ( i = 0; i < numberTraces; ++i) {

		fileName = malloc ( sizeof (char) + strlen(baseName) + 100 );
		sprintf ( fileName ,
				 baseName , i 
		);

		command = malloc( sizeof (char) + ( strlen (baseCommand) + strlen (pwd) + strlen (fileName) + (14*8)));
		sprintf (    command,
					baseCommand,
					traceData[i].MUESTRAS ,
					traceData[i].inter_arrival,
					traceData[i].DISTRIBUTION ,
					traceData[i].mean ,
					traceData[i].stddev ,
					traceData[i].TEMPERATURE ,
					traceData[i].stddevT ,
					traceData[i].PRESION ,
					traceData[i].stddevP ,
					traceData[i].HUMEDITY ,
					traceData[i].stddevH ,
					traceData[i].RADSOLAR ,
					traceData[i].stddevR ,
					traceData[i].Concurrency ,
					pwd , fileName
		);

        execute_command ( command );
		
		free ( command );
		free ( fileName );
	}

}


void execute_command ( char * command) {
	FILE                *fp;
    fp = popen ( command , "r" ) ;
    if ( fp == NULL ) {
        printf("Failed to run command to execute trace_generator container\n");
        exit(1);
    }
    pclose(fp);
}


/**
 * @brief Function that allows to obtain size of the file.
 */
long fileSize( char *fname ) {
    long              ftam = -1;
    struct stat       fdata;
    int               error;

    ftam = -1;
    error= stat(fname,&fdata);
    if (error >= 0)
        ftam = fdata.st_size;
    else
        printf("FileName: %s ERRNO: %d - %s\n", fname, errno, strerror(errno));
    
    return ftam;
}

/**
 * @brief Function to load balanced.
 */
struct worker * assignation( struct config *configuration ,  struct traceConfig *traceData ) {
    struct config               *config;
    struct worker               *arrayWorkers;
    struct traces               *traces;
    char                        *baseName, *fileName, *pwd;
    int                         contar[configuration->workers], i , c, position, position1, position2;


    config = malloc ( sizeof ( struct config ));
    arrayWorkers = ( struct worker* ) malloc ( configuration->workers * sizeof ( struct worker ));

    for (int i = 0; i <  configuration->workers; ++i){
        contar[i] = 0;
        arrayWorkers[i].id = i;
        arrayWorkers[i].sizeWorker = 0;
        arrayWorkers[i].sizeStorage = 0;
        arrayWorkers[i].interarrive = 0;
        arrayWorkers[i].trace =  malloc ( sizeof ( struct traces ) * configuration->traces_number * 2); 
    }

    c = 0;
    position = 0;
    position1 = 0;
    position2 = 0;
    srand ( time ( NULL ));            //< Initialization, should only be called once.

    pwd = getenv("PWD");
    baseName = malloc( sizeof ( char ) * 50 + strlen (pwd) );
    baseName = "%s/traces/trace%d.txt";

    traces = malloc(sizeof(struct traces) * configuration->traces_number);

    
    for ( i = 0 ; i < configuration->traces_number ; ++i) {
        fileName = malloc ( sizeof (char) + strlen(baseName) + 150 );
        sprintf ( fileName ,
                 baseName , pwd , i 
        );
        //printf("%s\n", fileName);
        traces[i].traceName = fileName;
        traces[i].size = fileSize(fileName);
        traces[i].mean_interarrival = traceData[i].inter_arrival;
        traces[i].MUESTRAS = traceData[i].MUESTRAS;
    }

    for ( i = 0 ; i < configuration->traces_number ; ++i) {
        position1 = rand() % configuration->workers; //< Returns a pseudo-random integer between 0 and RAND_MAX.
        position2 = rand() % configuration->workers;

        if ( configuration->workers == 1 )
          position = position1;
        else {
          while( position1 == position2 )
            position2 = rand() % configuration->workers;
          

          if (arrayWorkers[position1].sizeStorage > arrayWorkers[position2].sizeStorage)
            position = position2;
          else 
            position = position1;
        }

        //printf("Position %d  Contar %d\n", position, contar[position]);
        arrayWorkers[position].sizeStorage += traces[i].size;
        arrayWorkers[position].sizeWorker++;
        arrayWorkers[position].trace[contar[position]] = traces[i];
        contar[position]++;
    }
    
    
    return arrayWorkers;
}

/**
 * @brief Function that deploy threads in the pattern.
 */
void deployThread_stages(struct config * configuration, struct worker *arrayWorkers, int stageNumber) {
  int                   rc, i;
  char                  *command;
  FILE                  *fp;
  pthread_t             threads[configuration->workers]; //< Array with strcture belongs to the threads.

  i = 0;

  switch ( stageNumber ) {
    case 1:
        for ( i = 0; i < configuration->workers ; i++ ) 
            arrayWorkers[i].stage = 1;
        break;
    case 2:
        for ( i = 0; i < configuration->workers ; i++ ) 
            arrayWorkers[i].stage = 2;
        break;
    case 3:
        for ( i = 0; i < configuration->workers ; i++ ) 
            arrayWorkers[i].stage = 3;
        break;
    case 4:
        for ( i = 0; i < configuration->workers ; i++ ) 
            arrayWorkers[i].stage = 4;
        break;
    case 5:
        for ( i = 0; i < configuration->workers ; i++ ) 
            arrayWorkers[i].stage = 5;
        break;
  }


  for ( i = 0; i < configuration->workers ; i++ ) {  //< Make threads
    rc = pthread_create (           //< Make object
      &threads[i],
      NULL,
      sendWorkstage,
      (void *) &arrayWorkers[i]     //< Thread data convert to a pointer
    );

    if (rc) {
      printf ( "\tMaster ERROR; return code from pthread_create() is %d\n", rc );
      exit(-1);
    }
  }

  for (i = 0; i<  configuration->workers ; ++i) {     //< Block until all threads complete
    pthread_join(threads[i], NULL);
  }
}


/**
 * @brief Function that send work to workers.
 */
void *sendWorkstage ( void *threadarg ) {
  struct worker           *my_data;

  my_data = (struct worker *) threadarg;   //< Conversion to structure attribute

  serviceTime ( my_data );

  pthread_exit(NULL);   //< Kill thread
}


void serviceTime ( struct worker * my_data ) {
     switch ( my_data->stage ) {
        case 1:
            compress_time ( my_data ) ;
            break;
        case 2:
            hashing_time ( my_data ) ;
            break;
        case 3:
            indexing_time ( my_data ) ;
            break;
        case 4:
            IDA_time ( my_data ) ;
            break;
        case 5:
            //upload_time ( my_data );
            break;
    }
} 

void compress_time ( struct worker * my_data ) {
    int                     j;
    char                    *command, *path;
    float                   st;

    st = 0;
    path = getenv("PWD");

    

    if (my_data->sizeWorker > 0) {
        for (j = 0; j < my_data->sizeWorker; ++j ) {   
            //printf("%s %ld, %d\n", my_data->trace[j].traceName, my_data->trace[j].size,128*1024);  
            if ( my_data->trace[j].size < (128*1024) ) {
               my_data->trace[j].service_time_c = 0;
            } else {
                //printf("%ld\n", my_data->trace[j].size);
                my_data->trace[j].service_time_c = compressStage (my_data->trace[j].size) ;

                command = malloc ( sizeof(char) * strlen (path) + (8*6 + 150) );
                sprintf(
                    command,
                    "docker exec single%d ./single %f %f %d >> \'%s/results/w%d_stage1.txt\'",
                    my_data->id,
                    my_data->trace[j].mean_interarrival,
                    my_data->trace[j].service_time_c,
                    my_data->sizeWorker,
                    path,
                    my_data->id
                );

                //printf("%s\n", command);

                execute_command ( command );
                free(command);
            }
        }
       
    }

}

void hashing_time ( struct worker * my_data ) {
    int                     j;
    char                    *command, *path;
    long unsigned           newSize;
    float                   st;

    st = 0;
    newSize = 0;

    path = getenv("PWD");

    if (my_data->sizeWorker > 0) {
        for (j = 0; j < my_data->sizeWorker; ++j ) {  
            //printf("\n\n%s Size trace : %ld\n", my_data->trace[j].traceName, my_data->trace[j].size);   
            if ( my_data->trace[j].size < 128*1024 ) {
                my_data->trace[j].service_time_h = hashingStage ( my_data->trace[j].size ) ;
            } else {
                newSize = compressStageSize (my_data->trace[j].size);
                my_data->trace[j].service_time_h = hashingStage ( newSize ) ;
            }

            command = malloc ( sizeof(char) * strlen (path) + (8*6 + 150) );
            sprintf(
                command,
                "docker exec single%d ./single %f %f %d >> \'%s/results/w%d_stage2.txt\'",
                my_data->id,
                my_data->trace[j].mean_interarrival,
                my_data->trace[j].service_time_h,
                my_data->sizeWorker,
                path,
                my_data->id
            );       

            //printf("%s\n", command);

            execute_command ( command );
            free(command);
        }
    } 
  
}

void indexing_time ( struct worker * my_data ) {
    int                     j;
    char                    *command, *path;
    long unsigned           newSize;
    float                   st;


    st = 0;
    newSize = 0;
    my_data->service_time = 0;
    path = getenv("PWD");
    if (my_data->sizeWorker > 0) {
        st = indexingStage (  my_data->sizeWorker ) ;
        command = malloc ( sizeof(char) * strlen (path) + (8*6 + 150) );
        sprintf(
            command,
            "docker exec single%d ./single %f %f %d >> \'%s/results/w%d_stage3.txt\'",
            my_data->id,
            my_data->trace[0].mean_interarrival,
            st,
            my_data->sizeWorker,
            path,
            my_data->id
        );       

        //printf("%s\n", command);

        my_data->service_time = st;
        execute_command ( command );
        free(command);
    }

}


void IDA_time ( struct worker * my_data ) {
    int                     j;
    char                    *command, *path;
    long unsigned           newSize;
    float                   st;

    st = 0;
    newSize = 0;

    path = getenv("PWD");

    if (my_data->sizeWorker > 0) {
        //printf("%d\n",my_data->sizeWorker);
        for (j = 0; j < my_data->sizeWorker; ++j ) {     
            if ( my_data->trace[j].size < 128*1024 ) {
                my_data->trace[j].service_time_i = IDAStage ( my_data->trace[j].size ) ;
            } else {
                newSize = compressStageSize (my_data->trace[j].size);
                my_data->trace[j].service_time_i = IDAStage ( newSize ) ;
            }

            command = malloc ( sizeof(char) * strlen (path) + (8*6 + 150) );
            sprintf(
                command,
                "docker exec single%d ./single %f %f %d >> \'%s/results/w%d_stage4.txt\'",
                my_data->id,
                my_data->trace[j].mean_interarrival,
                my_data->trace[j].service_time_i,
                my_data->sizeWorker,
                path,
                my_data->id
            );       

            //printf("%s\n", command);

            execute_command ( command );
            free(command);
        }

    }
}

//Reconsiderar ... hay que agregar 1.66666 mas de tamaño original
/*void upload_time ( struct worker * my_data ) {
    int                     j;
    char                    *command, *path;
    long unsigned           newSize;

    newSize = 0;

    path = getenv("PWD");
    for (j = 0; j < my_data->sizeWorker; ++j ) {     
        if ( my_data->trace[j].size < 128*1024 ) {
            my_data->trace[j].service_time = uploadStage ( my_data->trace[j].size ) ;
        } else {
            newSize = compressStageSize (my_data->trace[j].size);
            my_data->trace[j].service_time = uploadStage ( newSize ) ;
        }

        command = malloc ( sizeof(char) * strlen (path) + (8*6 + 150) );
        sprintf(
            command,
            "docker exec single%d ./single %f %f %lld >> \'%s/results/w%d_stage3.txt\'",
            my_data->id,
            my_data->trace[j].mean_interarrival,
            my_data->trace[j].service_time,
            my_data->trace[j].MUESTRAS,
            path,
            my_data->id
        );       

        execute_command ( command );
        free(command);
    }
}*/









  /*command = malloc(strlen(getenv("PWD")) + 50 *sizeof(char));

  strcpy(command, "");
  sprintf( command,"mkdir -p %s/FilesCompress", getenv("PWD") );

  fp = popen(command, "r");
  if (fp == NULL) {
     printf("Failed to run command\n" );
     exit(1);
  }
  pclose(fp);

  free(command);


  command = malloc(strlen(getenv("PWD")) + 50 *sizeof(char));
  strcpy(command, "");
  sprintf( command,"chmod 777 -R %s/FilesCompress", getenv("PWD") );

  fp = popen(command, "r");
  if (fp == NULL) {
     printf("Failed to run command\n" );
     exit(1);
  }
  pclose(fp);
  free(command);*/