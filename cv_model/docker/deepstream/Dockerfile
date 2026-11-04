# DeepStream 9.0 (DGX Spark / SBSA) + Python bindings (built from source) + MQTT client
FROM nvcr.io/nvidia/deepstream:9.0-triton-sbsa-dgx-spark
RUN /opt/nvidia/deepstream/deepstream/user_deepstream_python_apps_install.sh --build-bindings
# the installer puts pyds in its own venv; make it the default python
ENV VIRTUAL_ENV=/opt/nvidia/deepstream/deepstream/sources/deepstream_python_apps/pyds
ENV PATH=$VIRTUAL_ENV/bin:$PATH
RUN pip install --no-cache-dir paho-mqtt==2.1.0 numpy opencv-python-headless
# pyds needs the driver libs the NVIDIA runtime mounts, so check it at run time:
#   docker run --rm --gpus all cv-defect/deepstream:9.0 python -c "import pyds"
WORKDIR /workspace
