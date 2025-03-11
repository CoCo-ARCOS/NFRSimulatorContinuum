#include "generator.h"

int main ( int argc , char **argv ) {
	//PARAMETERS OF FORM	
	assignation ( argv ) ;	
	long long unsigned 			bufferin[BUFFER],bufferout[BUFFER],current_time_c[Concurrency];
	struct traza 				print_traza[BUFFER], traza_con[BUFFER];
	
	// STRUCT INICIALIZATION
	inicialization ( print_traza , traza_con , current_time_c);
	RandTimeInit();

	//TRACE GENERATION
	makeTrace ( bufferin,  current_time_c ,  traza_con );
		
	return 0;
}
