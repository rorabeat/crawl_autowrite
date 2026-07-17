#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
레거시 진입점. 실제 빌드는 build_exe.py로 위임한다.
"""

import sys

from build_exe import main


if __name__ == "__main__":
    sys.exit(main())
