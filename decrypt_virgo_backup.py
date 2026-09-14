"""Decrypt a Virgo AES-256-GCM backup, verifying authentication before publishing output."""
import json,os,sys
from pathlib import Path
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes

def main():
    if len(sys.argv)!=4:raise SystemExit('Usage: python decrypt_virgo_backup.py ARCHIVE.zip.aes KEY.json OUTPUT.zip')
    source,keyfile,dest=map(Path,sys.argv[1:])
    key=bytes.fromhex(json.loads(keyfile.read_text(encoding='utf-8'))['key_hex'])
    if dest.exists():raise SystemExit('Output already exists; choose a new filename.')
    temp=dest.with_name(dest.name+'.partial')
    if temp.exists():raise SystemExit('Partial output exists; choose a new filename.')
    created=False
    try:
        with source.open('rb') as src:
            header=src.read(20)
            if len(header)!=20 or header[:8]!=b'VIRGOBK1':raise ValueError('Not a Virgo backup')
            size=source.stat().st_size
            if size<36:raise ValueError('Truncated backup')
            src.seek(-16,2);tag=src.read(16);src.seek(20)
            dec=Cipher(algorithms.AES(key),modes.GCM(header[8:],tag)).decryptor();dec.authenticate_additional_data(header)
            remaining=size-36
            with temp.open('xb') as out:
                created=True
                while remaining:
                    block=src.read(min(4*1024*1024,remaining))
                    if not block:raise ValueError('Unexpected end of backup')
                    remaining-=len(block);out.write(dec.update(block))
                out.write(dec.finalize())
        temp.rename(dest)
        print('Decrypted and authenticated:',dest)
    except BaseException:
        if created and temp.exists():temp.unlink()
        raise

if __name__=='__main__':main()
