/**
 * @file simulator.h
 * @mainpage Simulator to Preparation and retrieval service
 * @author Diana E. Carrizales-Espinoza
 * @date November 2019
 */

#include <stdlib.h>
#include <stdio.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <dirent.h>
#include <string.h>
#include <errno.h>
#include <time.h>
#include <unistd.h>
#include <pthread.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sys/ipc.h>
#include <sys/time.h>
#include <sys/shm.h>
#include <fcntl.h>
#include <libgen.h>
#include "string.h"

float interpolation( float x, float x0, float x1, float y0, float y1) ;

float compressStage (long unsigned filesize) ;

long unsigned compressStageSize ( long unsigned filesize ) ;

float hashingStage (long unsigned filesize) ;

float indexingStage (long numFiles) ;

float IDAStage (long unsigned filesize) ;

//float uploadStage (long long unsigned filesize) ;