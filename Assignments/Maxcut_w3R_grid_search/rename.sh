for f in *_custom*; do
        mv -- "$f" "${f/_custom__steps_2/_custom-2}"
done
