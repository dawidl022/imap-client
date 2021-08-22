import pathlib
from setuptools import setup, find_packages


HERE = pathlib.Path(__file__).parent

README = (HERE / "README.md").read_text()


setup(
    name="imap-client",
    version="0.1.6",
    description="Simple client providing an object interface for imaplib",
    long_description=README,
    long_description_content_type='text/markdown',
    packages=find_packages(),
    url="https://github.com/dawidl022/imap-client",
    author="dawidl022",
    license="MIT"
)
