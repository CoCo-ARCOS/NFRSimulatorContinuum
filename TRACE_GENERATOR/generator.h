#include "stdio.h"
#include "stdlib.h"
#include "math.h"
#include "statlib.h"
#include "genericlib.h"


#define ranf() ((float)rand()/(float)RAND_MAX)
#define TYPES 50
#define RANGE div(BUFFER,2) 

//FUNCTIONS
double poisson(float , float);
float  normal(float , float);

//GENERAL VARS
int   					i , j  , a , c , ct , num , found , cnt , count, stddev , mean , stddevS, 
						inter_arrival , Concurrency , BUFFER , SIZE, DISTRIBUTION ;
long long unsigned	 	MUESTRAS, current_time , sum ;
double 					r, b, y , sz;

//STRUCTS DECLARATION
struct traza {
   long long unsigned   interarrival;
   long long unsigned 	size;
};

void assignation ( char **argv ) ;

void inicialization ( struct traza *trace, struct traza *traza_con , long long unsigned * current_time_c ) ;

double poisson ( float lambda, float x ) ;

float normal ( float m, float s ) ;

void quicksort ( long long unsigned arr[], int low, int high ) ;

double atmosParamP ( double b , int param , long long unsigned sum , int ct ) ;

double atmosParamN ( double mean , int  stddev ) ;

double atmosParamU ( double param );

void dataConcurrent ( int i  , long long unsigned * current_time_c) ;

void sensorsData ( int distribution, double * sz ) ;

void makeTrace (  long long unsigned * bufferin, long long unsigned * current_time_c , struct traza * traza_con ) ;

void makeSensorData ( long long unsigned * bufferin , long long unsigned * current_time_c , struct traza * traza_con) ;
