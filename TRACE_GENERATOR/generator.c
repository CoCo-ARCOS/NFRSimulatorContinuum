#include "generator.h"

void assignation ( char **argv ) {
		BUFFER = 0;
		ct = 0;
		found = FALSE;
		cnt = 0;

		current_time = 0;
		sum = 0;
		r = 0;

		MUESTRAS = 0;
		Concurrency = 0;
		inter_arrival = 0;
		DISTRIBUTION = 0;
		
		count = 0;
		stddev = 0;
		mean = 0;

		stddevS = 0;
		

		MUESTRAS = atol(argv[1]);
		inter_arrival = atoi(argv[2]);
		DISTRIBUTION = atoi(argv[3]);
		mean = atof(argv[4]);
		stddev = atof(argv[5]);

		SIZE = atoi(argv[6]);
		stddevS = atof(argv[7]);
		
		
		Concurrency = atoi(argv[8]);
		BUFFER = 50*Concurrency;
}

void inicialization ( struct traza *print_traza , struct traza *traza_con , long long unsigned * current_time_c ) {
	
	for ( i=0 ; i < Concurrency ; i++ ) {
		//current_t[i]=0;
		print_traza[i].interarrival = 0;
   		print_traza[i].size = 0; 

		traza_con[i].interarrival = 0;
		current_time_c[i] = 0;
	}
}

double poisson ( float lambda, float x ) {
	return ( exp ( -lambda ) * pow ( lambda , x ) / exp ( logfact ( x ) ) );
}

float normal(float m, float s) {				        
	float			 x1, x2, w, y1;
	static float     y2;
	static int 		 use_last;

	use_last = 0;

	if (use_last) {	                 /* < use value from previous call */
		y1 = y2;
		use_last = 0;
	}
	else {
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
	int i, j, y, z;

	i = low;
	j = high;
	y = 0;
	
	/* compare value */
	z = arr[(low + high) / 2];

	/* partition */
	do {
		
		while(arr[i] < z) i++;			/*< find member above ... >*/

		while(arr[j] > z) j--;			/*< find element below ... >*/

		if(i <= j) {					/*< swap two elements >*/
			y = arr[i];
			arr[i] = arr[j];
			arr[j] = y;
			
			i++;
			j--;
		}
	} while(i <= j);


	/* recurse */
	if ( low < j )
		quicksort ( arr, low, j ) ;

	if ( i < high )
		quicksort ( arr, i, high ) ;
}

double atmosParamP ( double b , int param , long long unsigned sum , int ct ) {
	int 				x;
	x = 0;
	do {
	  	b = BestRand(1) -1;
	  	sum = BestRand ( param * 2 );
		if ( b  <= poisson ( param , sum ))
		{
			x=sum;
			ct=0;
		} else
			ct++;
	} while ( ct != 0 );

	return x;
}

double atmosParamN ( double mean , int  stddev ) {
	int     			x;
	x = 0;
	do {
		x = normal(mean,stddev);
	} while(x < 0);	

	return x;
}

double atmosParamU ( double param ){
	int 				x;
	x = 0;

	x = BestRand(param);

	return x;
}

void dataConcurrent ( int i , long long unsigned * current_time_c) {
	/*switch ( DISTRIBUTION ) {
		
		//UNIFORM DISTRIBUTION
		case 1:
			r = BestRand ( inter_arrival ) ;
			if ( Concurrency > 1 )
			    current_time_c[i] += r * 1000;
			else
				current_time += r * 1000;
			ct = 0;	
			break;
		
		//POISSON DISTRIBUTION
		case 2:
			do {
				y = BestRand ( 1 ) - 1; // para que sea entre 0 y 1.	
				r = BestRand ( inter_arrival * 2 );
				if ( y  <= poisson ( inter_arrival , r ) ) {
					if ( Concurrency > 1 )
				    	current_time_c[i] += r * 1000;
				    else
						current_time += r * 1000;
					ct = 0;
				}
				else
					ct ++;
			} while ( ct != 0 );
			break;
		
		//NORMAL DISTRIBUTION
		case 3:
			//SOURCE CODE FOR NORMAL DISTRIBUTION
			r = BestRand ( inter_arrival );	
			if (b <= normal ( mean , stddev ) ) {	
				if ( Concurrency > 1 )
			    	current_time_c[i] += r * 1000;
				else
					current_time += r * 1000;
				ct = 0;
			}	
		break;
	 //END SWITCH 1
    }	*/

    if ( Concurrency > 1 )
    	current_time_c[i] += inter_arrival;
    else
		current_time += inter_arrival;


}

void sensorsData ( int distribution, double * sz ) {
	switch(DISTRIBUTION) {
		//UNIFORME DISTRIBUTION SOURCE CODE
		case 1:
			//UNIFORME DISTRIBUTION LINES TO DETERMINATE A REQUEST SIZE
 			*sz = atmosParamU ( SIZE );
			break;
		//POISSON DISTRIBUTION SOURCE CODE	
		case 2:
			//POISSON DISTRIBUTION LINES TO DETERMINATE A REQUEST SIZE
			*sz = atmosParamP ( b , SIZE , sum , ct );
			break;
		//NORMAL DISTRIBUTION SOURCE CODE
		case 3:
			//NORMAL DISTRIBUTION LINES FOR DETERMINATE A REQUEST SIZE
		 	*sz = atmosParamN ( SIZE ,  stddevS ) ;
			break;
	}//END SWITCH 2
}

void makeTrace (  long long unsigned * bufferin, long long unsigned * current_time_c , struct traza * traza_con ) {
	while ( !found ) {
		if( count == BUFFER ) {
			quicksort ( bufferin , 0 , BUFFER ) ;
			count = 0;
			for ( i = 0 ; i < BUFFER ; i++)    
			    for ( j = 0 ; j < BUFFER ; j++)
				   if (bufferin[i]==traza_con[j].interarrival) {   
			  	    	printf ("%llu %llu\n",
			  	    		   traza_con[j].interarrival,
			  	    		   traza_con[j].size);
				   }	    
			j=0;	
		}

		makeSensorData (bufferin , current_time_c ,  traza_con) ;

	}//END WHILE
}

void makeSensorData (  long long unsigned * bufferin, long long unsigned * current_time_c , struct traza * traza_con) {
	for ( i = 0 ; i < Concurrency ; i++ ) {
		dataConcurrent ( i , current_time_c) ;
	    sensorsData ( DISTRIBUTION , &sz );

		//ASSIGNMENT VALUES TO BUFFERINg AND TRAZA_CON
		if ( Concurrency > 1 ) {    
		    bufferin[j] = current_time_c[i];
		    traza_con[j].interarrival = current_time_c[i];
		} else {
			bufferin[j] = current_time; 
		    traza_con[j].interarrival = current_time;
		}
		
		traza_con[j].size = sz;

		j++;
		if (cnt < MUESTRAS) 
			cnt++; 
		else 
			found=1;
		count++;
	}//END for concurrency
}