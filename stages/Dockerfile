#CI base
FROM ubuntu:16.04 
#Instalacion de las dependencias de software 
RUN apt-get update && apt-get install build-essential -y && apt-get install python -y
#Se copia el Codigo de las Aplicaciones a la imagen de contenedor
COPY . /home/single/
WORKDIR /home/single/
RUN make