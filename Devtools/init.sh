GIT_ROOT=$(git rev-parse --show-toplevel)

if [ "$GIT_ROOT" == "." ]; then
    GIT_ROOT=$(pwd)
fi

python3 -m venv  $GIT_ROOT/.venv
source $GIT_ROOT/.venv/bin/activate

pip install -r $GIT_ROOT/requirements.txt