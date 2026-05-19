
#muestras
for i in 10 100 1000
do
    for j in 1 3 6 12
    do
        echo "Ejecutando $i muestras, $j trabajadores"
        python genera.py $i 10000 5000 $j
        ./main > "new_results/"$i"t_"$j"w.txt" 2> /dev/null
        mkdir -p "new_results/"$i"t/"$j"w/"
        mv results/* "new_results/"$i"t/"$j"w"
    done
done


