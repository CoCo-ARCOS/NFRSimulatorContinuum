#include "stdlib.h"
#include "time.h"

void RandTimeInit(void){
   srand(time(NULL));
}

float BestRand(double max){
  return(1+max*rand()/(RAND_MAX+1.0));
}
