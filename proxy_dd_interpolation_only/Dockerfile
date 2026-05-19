#CI base
FROM ddomizzi/docker:ubuntu
#Instalacion de las dependencias de software 
RUN apk add build-base && apk add python 
#Se copia el Codigo de las Aplicaciones a la imagen de contenedor
COPY . /home/proxy/
WORKDIR /home/proxy/
RUN make