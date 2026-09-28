#!/bin/zsh
# Regenerates the test audio files from phrases.tsv, with the macOS voices (say).
# Columns: number, voice, spoken text, expected text. Usage: ./generate.sh
set -e
cd "${0:A:h}"
while IFS=$'\t' read -r n voice spoken expected; do
  say -v "$voice" -o "$n.aiff" "$spoken"
  ffmpeg -loglevel error -y -i "$n.aiff" -ar 16000 -ac 1 "$n.wav"
  rm "$n.aiff"
done < phrases.tsv
echo "$(ls *.wav | wc -l | tr -d ' ') files generated"
