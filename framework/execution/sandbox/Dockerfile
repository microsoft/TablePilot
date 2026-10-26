FROM python:3.11-slim

RUN pip install --no-cache-dir pandas==2.2.3 matplotlib==3.10.1 scikit-learn==1.6.1 scipy==1.15.3 seaborn==0.13.2 statsmodels==0.14.4
COPY worker.py /opt/tablepilot/worker.py
ENV MPLBACKEND=Agg MPLCONFIGDIR=/tmp/matplotlib OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 HOME=/tmp
WORKDIR /work
USER 65532:65532
ENTRYPOINT ["python", "-I", "-B", "/opt/tablepilot/worker.py"]