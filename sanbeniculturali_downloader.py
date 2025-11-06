"""Main starting script"""

import typer

from data.downloader import get_archives_list

def print_results():
    for i, elem in enumerate(get_archives_list()):
        print(f"{i}-{elem[41:-1].replace("-", " ")}")

if __name__ == "__main__":
    typer.run(print_results)