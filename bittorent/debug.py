DEBUG = True

GREY = "\x1b[30m"
RED = "\x1b[31m"
GREEN = "\x1b[32m"
YELLOW = "\x1b[33m"
BLUE = "\x1b[34m"
RESET = "\x1b[0m"

def grey(s: str) -> str:
    return f"{GREY}{s}{RESET}"

def red(s: str) -> str:
    return f"{RED}{s}{RESET}"

def green(s: str) -> str:
    return f"{GREEN}{s}{RESET}"

def yellow(s: str) -> str:
    return f"{YELLOW}{s}{RESET}"

def blue(s: str) -> str:
    return f"{BLUE}{s}{RESET}"