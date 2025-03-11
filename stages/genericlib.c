#include "math.h"
#include "stdio.h"

/* Factorial Long Int */
long lrfact(int x){
   if (x < 2)
     return(1);
   else
     return(x * lrfact(x-1));  
}

/* Factorial Log */
double logfact(int fact){
   if (fact<=1)
      return(0);
   else
      return(log(fact) + logfact(fact-1));
}

/* Factorial float */
float frfact(int x){
   if (x < 2)
     return(1);
   else
     return(x * lrfact(x-1));  
}

/* Factorial double */
double drfact(int x){
   if (x < 2)
     return(1);
   else
     return(x * lrfact(x-1));  
}

