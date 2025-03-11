/**
 * @file service_time.c
 * @mainpage Simulator to Preparation and retrieval service
 * @author Diana E. Carrizales-Espinoza
 * @date November 2019
 */

 #include "service_time.h"

float interpolation( float x, float x0, float x1, float y0, float y1){
	float 			y;

	y = 0;
	y = (float)((x-x1)/(x0-x1))*(y0-y1)+y1;
	return y;
}

float compressStage (long unsigned filesize) {
	float 			                compressTime;
	long 			                  i, y, c;

	
	float tested[9][2]={
              {1,  267},
              {10,  270},
              {100, 251},
              {1024,  265},
              {10240, 276},
              {102400,  266},
              {1045504, 276},
              {10455040,  328},
              {104857620, 586},
   	};



	compressTime = 0;
	c = 0;

	for (i = 0; i < 1; ++i){
		for (y = 0; y < 9; ++y){
			if ( c == 0) {
				if (filesize < tested[y][0]){
					compressTime = compressTime +interpolation( filesize, 
													tested[y][0], tested[y-1][0],
													tested[y][1], tested[y-1][1]);

					c=1;

          //printf("original size: %ld compressTime: %f\n", filesize , compressTime);
				}
			}
		}
		c = 0;
	}

	return compressTime;
}

long unsigned compressStageSize (long unsigned filesize) {
  long unsigned               compressSize;
  long                        i, y, c;

  
  float tested[9][2]={
              {1  ,20} ,
              {10  ,  29} ,
              {100  , 114} ,
              {1024  ,  455} ,
              {10240  , 3035} ,
              {102400  ,  27039} ,
              {1045504  , 255091} ,
              {10455040  ,  2384482} ,
              {104857620  , 23843770} ,
    };

  compressSize = 0;
  c = 0;

  for (i = 0; i < 1; ++i){
    for (y = 0; y < 9; ++y){
      if ( c == 0) {
        if (filesize < tested[y][0]){
          compressSize = (long)interpolation( filesize, 
                          tested[y][0], tested[y-1][0],
                          tested[y][1], tested[y-1][1]);

          c=1;

          //printf("original size: %ld compressSize: %ld \n", filesize , compressSize );
        }
      }
    }
    c = 0;
  }

  return compressSize;
}

//detalles de sensores, de fuentes climatologicas y las pruebas. Sábado el paper revisado y el lunes todos los resultados 


float hashingStage (long  unsigned filesize) {
  float                       hashingTime;
  long                        i, y, c;

  
  float tested[9][2]={
              {1 ,  2} ,
              {10 , 2} ,
              {100 ,  2} ,
              {1024 , 3} ,
              {10240 ,  3} ,
              {102400 , 3} ,
              {1048576 ,  8} ,
              {10485762 , 45} ,
              {104857620 ,  327} ,
    };



  hashingTime = 0;
  c = 0;

  for (i = 0; i < 1; ++i){
    for (y = 0; y < 9; ++y){
      if ( c == 0) {
        if (filesize < tested[y][0]){
          hashingTime = hashingTime +interpolation( filesize, 
                          tested[y][0], tested[y-1][0],
                          tested[y][1], tested[y-1][1]);
          c=1;
 
          //printf("Size File: %ld hashingTime: %f\n", filesize , hashingTime);
        }
      }
    }
    c = 0;
  }

  return hashingTime;
}

float indexingStage (long numFiles) {
  float                       indexingTime;
  long                        i, y, c;

  
  float tested[10][2]={
              {1,296},
              {10,375},
              {100,29584},
              {200, 30405},
              {500, 35637},
              {1000,38197},
              {2000,40456},
              {4000,43457},
              {6000,44567},
              {10000,108946}
    };

  indexingTime = 0;
  c = 0;

  for (i = 0; i < 1; ++i){
    for (y = 0; y < 10; ++y){
      if ( c == 0) {
        if (numFiles < tested[y][0]){
          indexingTime = indexingTime +interpolation( numFiles, 
                          tested[y][0], tested[y-1][0],
                          tested[y][1], tested[y-1][1]);


          c=1;

          //printf("Size File: %ld indexingTime: %f\n", filesize , indexingTime);
        }
      }
    }
    c = 0;
  }

  return indexingTime;
}


float IDAStage (long  unsigned filesize) {
  float                       IDATime;
  long                        i, y, c;

  
  float tested[9][2]={
              {1 , 261} ,
              {10 , 269} ,
              {100 , 290} ,
              {1024 ,  280} ,
              {10240 , 288} ,
              {102400 ,  296} ,
              {1045504 , 351} ,
              {10455040 ,  1092} ,
              {104857620  , 7350} ,
    };


  IDATime = 0;
  c = 0;

  for (i = 0; i < 1; ++i){
    for (y = 0; y < 9; ++y){
      if ( c == 0) {
        if (filesize < tested[y][0]){
          IDATime = IDATime +interpolation( filesize, 
                          tested[y][0], tested[y-1][0],
                          tested[y][1], tested[y-1][1]);


          c=1;

          //printf("Size File: %ld IDATime: %f\n", filesize , IDATime);
        }
      }
    }
    c = 0;
  }

  return IDATime;
}


/*float uploadStage (long  unsigned filesize) {
  float                       uploadTime;
  long                        i, y, c;

  
  float tested[7][2]={
     { 1048576, 12568 } ,
     { 33554432, 236581 } ,
     { 67108864, 418350 } ,
     { 134217728, 806219 } ,
     { 268435456, 1545408 } ,
     { 536870912, 3048114 } ,
     { 1073741824, 6055735 } ,
    };

  uploadTime = 0;
  c = 0;

  for (i = 0; i < 1; ++i){
    for (y = 0; y < 7; ++y){
      if ( c == 0) {
        if (filesize < tested[y][0]){
          uploadTime = uploadTime +interpolation( filesize, 
                          tested[y][0], tested[y-1][0],
                          tested[y][1], tested[y-1][1]);


          c=1;

          printf("Size File: %ld uploadTime: %f\n", filesize , uploadTime);
        }
      }
    }
    c = 0;
  }

  return uploadTime;
}*/