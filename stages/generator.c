#include "stdio.h"
#include "stdlib.h"
#include "math.h"
#include "statlib.h"
#include "genericlib.h"


#define ranf() ((float)rand()/(float)RAND_MAX)
#define TYPES 50
#define RANGE div(BUFFER,2) 
//Número de bloques (Tamaño del arreglo en bloques)
//#define SAS_SIZE 549093

//FUNCTIONS
double poisson(float , float);
float  normal(float , float);
void quicksort(long long unsigned arr[], int low, int high);
//STRUCTS DECLARATION
struct traza {
   long long unsigned   interarrival;
   char			types;
   int			system; //default be 0
   long long unsigned   location;
   long long unsigned 	portion;
};

//MAIN
void main(int argc, char **argv)
{
	//GENERAL VARS
	int   BUFFER=0, ct_b_band=0,ct_b_long=0,a,c,ct=0,cont=0,num,found=FALSE,cnt=0,level=0,i,sec=0,burst_sec=0;
	long long unsigned current_time=0,prom=0,sum=0,current=0;
	char types;
	double r=0,time=0,y,x,v,z,type,b,offset;
	long MUESTRAS=0,SAS_SIZE=0;
	int Concurrency=0,j=0,PORTION=0,inter_arrival=0,DISTRIBUTION=0,read_ratio=0,count=0;
	int stddev=0,mean=0;
	//PARAMETERS OF FORM	
	MUESTRAS=atol(argv[1]);
	//tamaño del archivo
	PORTION=atoi(argv[2]);
	inter_arrival=atoi(argv[3]);
	//cantidad de datos que vas a leer ---> 1
	read_ratio=atoi(argv[4]);
	//tamaño 
	SAS_SIZE=atol(argv[5]);
	//Tipo de distribucion
	DISTRIBUTION=atoi(argv[6]);
	//media
	mean=atof(argv[7]);
	//Desviacion estandar
	stddev=atof(argv[8]);
	//Concurrencia
	Concurrency = atoi(argv[9]);
	BUFFER = 50*Concurrency;
	long long unsigned bufferin[BUFFER],bufferout[BUFFER],current_time_c[Concurrency];
	struct traza print_traza[BUFFER], traza_con[BUFFER];
	//concurrency=atoi(argv[9]);
// STRUCT INICIALIZATION
	for (i=0;i<Concurrency; i++)
	{
		//current_t[i]=0;
		print_traza[i].interarrival=0;
   		print_traza[i].location=0;
   		print_traza[i].portion=0; 
		print_traza[i].system=0;
		traza_con[i].interarrival=0;
   		traza_con[i].location=0;
   		traza_con[i].portion=0;
		traza_con[i].system=0;
		current_time_c[i]=0;
	}
	RandTimeInit();
	while (! found)
	{
		if(count==BUFFER)
		{
			quicksort(bufferin/*ARRAY*/,0/*LOW*/,BUFFER/*HIGH*/);
			count=0;
			for (i=0;i<BUFFER; i++)    
			    for (j=0;j<BUFFER; j++)
				   if (bufferin[i]==traza_con[j].interarrival)
				   {   
					/*print_traza[i].interarrival=traza_con[j].interarrival;
					print_traza[i].location=traza_con[j].location;
					print_traza[i].portion=traza_con[j].portion;
					print_traza[i].types=traza_con[j].types;
		*/		///NOTE this is required when using more than 1 storage systems.print_traza[i].system=traza_con[j].interarrival;  
			    printf ("%llu 0 %c %llu %llu\n",(unsigned long long) traza_con[j].interarrival,traza_con[j].types,(unsigned long long)traza_con[j].location,(unsigned long long)traza_con[j].portion);
				   }	    
		j=0;	
			/*************************** 
			1. COPY ORDERED FIELDS TO print_traza NOTE done
			2. Printing FROM O TO BUFFER-RANGO.... ALL FIELDS FROM print_traza NOTE Done 
			3. COPY FROM BUFFRER-RANGO TO BUFFER ALL DATA (FROM print_traza TO traza_con) ///NOTE this is your job... for today... 	
			*********************************/ 	
		}
		for (i=0;i<Concurrency; i++)
		{
		    switch(DISTRIBUTION)
		    {
					//UNIFORM DISTRIBUTION
			case 1:
				r=BestRand(inter_arrival);
				if (Concurrency>1)
				    current_time_c[i]+=r*1000;
				else
				current_time+=r*1000;
				ct=0;	
			break;
					//POISSON DISTRIBUTION
			case 2:
			do
   			{
				y=BestRand(1) -1; // para que sea entre 0 y 1.	
				r=BestRand(inter_arrival*2);
				if(y  <= poisson(inter_arrival,r))
				{
					
					if (Concurrency>1)
				    current_time_c[i]+=r*1000;
				    else
					current_time+=r*1000;
					ct=0;
				}
				else
					ct++;
			}while (ct!=0);
			break;
			//NORMAL DISTRIBUTION
			case 3:
						//SOURCE CODE FOR NORMAL DISTRIBUTION
				r=BestRand(inter_arrival);	
				if(b <= normal(mean,stddev))
				{	
					if (Concurrency>1)
				    current_time_c[i]+=r*1000;
					else
					current_time+=r*1000;
					ct=0;
				}	
			break;
		 //END SWITCH 1
		    }	  
		    switch(DISTRIBUTION)
		    {
				//UNIFORME DISTRIBUTION SOURCE CODE
			case 1:
				//UNIFORME DISTRIBUTION LINES TO DETERMINATE A REQUEST SIZE
			    x=BestRand(PORTION);
			break;
				//POISSON DISTRIBUTION SOURCE CODE	
			case 2:
				//POISSON DISTRIBUTION LINES TO DETERMINATE A REQUEST SIZE
			  do
			  {
			  	b=BestRand(1) -1;
			  	sum=BestRand(PORTION*2);
			  	if (b  <= poisson(PORTION,sum))
			  	{
					x=sum;
					ct=0;
				}
				else
					ct++;
					}while (ct!=0);
				break;
				//NORMAL DISTRIBUTION SOURCE CODE
				case 3:
					//NORMAL DISTRIBUTION LINES FOR DETERMINATE A REQUEST SIZE
					do
					{
			    			x = normal(mean,stddev);
					}while(x<0);	
				break;
			}//END SWITCH 2
		type=BestRand(100);
		if ((int)type>TYPES)
			types='w';
		else
			types='r';
		z=BestRand(SAS_SIZE);
//ASSIGNMENT VALUES TO BUFFERINg AND TRAZA_CON
		if (Concurrency>1)
		{    
	//	    printf ("%llu i->%d j->%d\n",(unsigned long long) current_time_c[i], i, j);
		    bufferin[j]=current_time_c[i];
		    traza_con[j].interarrival=current_time_c[i];
		}else
	        {  bufferin[j]=current_time; 
		    traza_con[j].interarrival=current_time;
		}
		traza_con[j].types=types;
		traza_con[j].system=0;
		traza_con[j].location=z;
		traza_con[j].portion=x;
		traza_con[j].types=types;
		j++;
		if (cnt < MUESTRAS) 
			cnt++; 
		else 
			found=1;
		count++;
	}//END for concurrency
///NOTE	printf ("%llu 0 %c %d %d\n",(unsigned long long) current_time,types,(int)z,(int)x);
	}//END WHILE
	//prom=sum/MUESTRAS;
//	printf ("Sum=%llu , prom=%llu MUESTRAS-->%d\n\n",sum,prom,MUESTRAS);
}//END MAIN

double poisson(float lambda, float x)
{
	return( exp(-lambda)*pow(lambda,x)/exp(logfact(x)) );
}

float normal(float m, float s)	
{				        
	float x1, x2, w, y1;
	static float y2;
	static int use_last = 0;

	if (use_last)		        /* use value from previous call */
	{
		y1 = y2;
		use_last = 0;
	}
	else
	{
		do {
			x1 = 2.0 * ranf() - 1.0;
			x2 = 2.0 * ranf() - 1.0;
			w = x1 * x1 + x2 * x2;
		} while ( w >= 1.0 );

		w = sqrt( (-2.0 * log( w ) ) / w );
		y1 = x1 * w;
		y2 = x2 * w;
		use_last = 1;
	}
	return( m + y1 * s );
}

void quicksort(long long unsigned arr[], int low, int high) {
 int i = low;
 int j = high;
 int y = 0;
 /* compare value */
 int z = arr[(low + high) / 2];

 /* partition */
 do {
  /* find member above ... */
  while(arr[i] < z) i++;

  /* find element below ... */
  while(arr[j] > z) j--;

  if(i <= j) {
   /* swap two elements */
   y = arr[i];
   arr[i] = arr[j];
   arr[j] = y;
//printf ("pos %d valued %llu pos %d valued %llu \n",i,(long long unsigned) arr[i],j, (long long unsigned) arr[j]);
   i++;
   j--;
  }
 } while(i <= j);


 /* recurse */
 if(low < j)
  quicksort(arr, low, j);

 if(i < high)
  quicksort(arr, i, high);
}

