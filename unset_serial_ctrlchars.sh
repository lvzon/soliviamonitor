#!/bin/sh

serial=$1

echo "Original TTY settings for $serial:"
stty -F $serial -g
stty -F $serial -a

stty -F $serial raw

stty -F $serial intr ^-
stty -F $serial quit ^-
stty -F $serial erase ^-
stty -F $serial kill ^-
stty -F $serial eof ^-
stty -F $serial eol ^-
stty -F $serial eol2 ^-
stty -F $serial swtch ^-
stty -F $serial start ^-
stty -F $serial stop ^-
stty -F $serial susp ^-
stty -F $serial rprnt ^-
stty -F $serial werase ^-
stty -F $serial lnext ^-
stty -F $serial discard ^-

echo "Modified TTY settings for $serial:"
stty -F $serial -g
stty -F $serial -a

