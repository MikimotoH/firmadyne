#!/bin/bash

set -u

brand=$1
fw_file=$2

## IID=$(sources/extractor/extractor.py -b "${brand}" -sql 127.0.0.1 -np -nk "${fw_file}" images | tee /dev/tty | grep  -E 'Database Image ID:' | grep -o -E '[0-9]+')
python -u sources/extractor/extractor.py -b "${brand}" -sql 127.0.0.1 -np -nk "${fw_file}" images | tee extraction.log
IID=`cat extraction.log | sed -r 's/.*Database Image ID: ([0-9]+)/\1/;tx;d;:x'`
[ -e images/$IID.tar.gz ] || { echo "images/$IID.tar.gz doesn't exist. Extraction failed."; exit 1 }
scripts/fw_files_to_psql.py $fw_file --brand $brand

scripts/getArch.sh images/${IID}.tar.gz
arch=$(scripts/psql_firmware.py "SELECT arch FROM image WHERE id=${IID};")
scripts/tar2db_tlsh.py images/${IID}.tar.gz
# scripts/tar2db.py -i ${IID} -f images/${IID}.tar.gz
sudo ./scripts/makeImage.sh ${IID} $arch
echo "inferNetwork.sh ${IID}"
scripts/inferNetwork.sh ${IID} $arch
net_infer_OK=$(scripts/psql_firmware.py "SELECT network_inferred FROM image WHERE id=${IID};")
if [ "$net_infer_OK" == "False" ] ; then
    exit 0
fi
scripts/test_network_reachable.py ${IID}
