#!/bin/bash
DOC=$(cat <<'END_HEREDOC'
  bootstrapbootstrap.sh

  Bootstraps bootstrap. Installs python/openssl/certificates so that
  bootstrap can bootstrap on distributions old enough to own several
  boots with which to wear straps on.

  Does not bootstrap zlib-dev (required for compile) or bzip2-dev
  (required for python's bzip2 module, which isn't required at the
  bootstrap stage but there are tests that will fail without it)

  Dependencies are installed into relative directory './bsbs'. The
  original bootstrap is installed into the current directory. Steps
  that are already completed won't be repeated.

  Currently only tested for CentOS5.

  Use like bootstrap:
    ./bootstrapbootstrap.sh --builder=dials hot update base

  Or just run on it's own to prepare the dependencies only:
   ./bootstrapbootstrap.sh

Usage:
  ./bootstrapbootstrap.sh                      Install dependencies only
  ./bootstrapbootstrap.sh ( --help | -h )      Display this message
  ./bootstrapbootstrap.sh [bootstrap options]  Install & run bootstrap
  ./bootstrapbootstrap.sh -- ( --help | -h )   Display bootstrap help

END_HEREDOC
)

set +e

# Handle help if they asked for it
if [[ $# -eq 1 ]]; then
  if [[ $1 == "--help" || $1 == "-h" ]]; then
    echo "$DOC"
    exit 0
  fi
fi
# Swallow "--" if passed it - this lets us ask for bootstrap help
if [[ $1 == "--" ]]; then
  shift
fi

# Save paths
ROOT=$(pwd)
openssl_dir=$ROOT/bsbs/build/openssl
python_build_dir=$ROOT/bsbs/build/python
python_dir=$ROOT/bsbs

# Install openssl
if [[ ! -f $openssl_dir/.done ]]; then
  echo "==================================="
  printf "Installing OpenSSL "
  sleep 1
  printf "."
  sleep 1
  printf "."
  sleep 1
  printf ".\n"

  # Remove if exists but incomplete
  if [[ -d $openssl_dir ]]; then
    rm -rf $openssl_dir
  fi
  # Build, but don't actually install (we'll use relative)
  mkdir -p $openssl_dir
  ( cd $openssl_dir
    curl -L https://www.openssl.org/source/openssl-1.0.2n.tar.gz | tar xz --strip-components=1
    ./config shared
    make
    touch .done)
fi

export LD_LIBRARY_PATH=$openssl_dir:$LD_LIBRARY_PATH

# Install python
if [[ ! -f $python_build_dir/.done ]]; then
  echo "==================================="
  printf "Installing Python "
  sleep 1
  printf "."
  sleep 1
  printf "."
  sleep 1
  printf ".\n"

  # Remove if exists but incomplete
  if [[ -d $python_build_dir ]]; then
    rm -rf $python_build_dir
  fi

  mkdir -p $python_build_dir
  ( cd $python_build_dir
    curl -L https://www.python.org/ftp/python/2.7.14/Python-2.7.14.tgz | tar xz --strip-components=1
    # Rewrite to use our custom openssl implementation
    sed -i "218,221 s/#//;s?/usr/local/ssl?$openssl_dir?;s/-L\$(SSL)\/lib /-L\$(SSL) /" Modules/Setup.dist && \
    ./configure --prefix=$python_dir
    make install
    # Install certifi as the system certificates are too old
    $ROOT/bsbs/bin/python -mensurepip
    $ROOT/bsbs/bin/pip install certifi

    touch .done )
fi

export PATH=$python_dir/bin:$PATH
export SSL_CERT_FILE=$(python -c "import certifi; print(certifi.where())")

# Fetch the original bootstrapping script
if [[ ! -f bootstrap.py ]]; then
  echo "==================================="
  echo "Fetching bootstrap"
  curl -L https://raw.githubusercontent.com/cctbx/cctbx_project/master/libtbx/auto_build/bootstrap.py > bootstrap.py
fi

# If the user only ran bare e.g. without bootstrap options, print a 'done'
# message. This is a divergence from the original bootstrap API which would
# do a default install. This is a special advanced-user case though.
if [[ -z "$@" ]]; then
  echo "Ready with $(python --version 2>&1)"
  echo " → Ready to run bootstrap😱; please re-run with bootstrap commands. "
  echo "   e.g.  './bootstrapbootstrap.sh --builder=dials hot update base'"
else
  # Pass through the arguments
  python bootstrap.py $@
fi

