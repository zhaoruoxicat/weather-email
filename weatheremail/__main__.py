"""支持 python3 -m weatheremail 方式调用。"""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
