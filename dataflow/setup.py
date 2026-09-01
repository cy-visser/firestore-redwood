import setuptools

setuptools.setup(
    name="redwood-firestore-cdc",
    version="1.0.0",
    install_requires=[
        "google-cloud-firestore>=2.20.0",
        "google-cloud-bigquery>=3.25.0",
        "google-auth>=2.30.0",
        "python-dotenv>=1.0.0",
    ],
    packages=setuptools.find_packages(),
    py_modules=["firestore_auth", "retail_catalog"],
)
