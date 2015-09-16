import os
from pipes import quote
from functools import update_wrapper, partial

from fabric.api import open_shell, prompt, warn
from fabric.colors import red
from fabric.contrib.files import upload_template

import managevm

def get_installdir():
    return os.path.dirname(managevm.__file__)

def cmd(cmd, *args, **kwargs):
    escaped_args = [quote(str(arg)) for arg in args]
    
    escaped_kwargs = {}
    for key, value in kwargs.iteritems():
        escaped_kwargs[key] = quote(str(value))

    return cmd.format(*escaped_args, **escaped_kwargs)

def fail_gracefully(fn, exc_class=SystemExit):
    def _wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except exc_class, e:
            while True:
                print
                print '(s) Drop to shell'
                print '(e) Exit'
                print '(c) Continue'

                answer = prompt('What do you want to do?', default='s')
                if answer == 'e':
                    raise e
                elif answer == 'c':
                    break
                elif answer == 's':
                    open_shell()
    return update_wrapper(_wrapper, fn)

def raise_failure(exc_obj):
    warn(red(unicode(exc_obj)))
    raise exc_obj

raise_failure = fail_gracefully(raise_failure, exc_class=Exception)

