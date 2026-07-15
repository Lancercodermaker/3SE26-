from glob import glob
from os.path import join

from setuptools import find_packages, setup


package_name = "sdr_receiver_py_wrapper"

setup(
    name=package_name,
    version="0.1.0",
    packages=find_packages(exclude=["test"]),
    include_package_data=True,
    package_data={
        package_name: ["vendor/*.py"],
    },
    data_files=[
        ("share/ament_index/resource_index/packages", [f"resource/{package_name}"]),
        (join("share", package_name), ["package.xml", "README.md", "requirements.txt"]),
        (join("share", package_name, "launch"), glob("launch/*.launch.py")),
        (join("share", package_name, "config"), glob("config/*.yaml")),
        (join("share", package_name, "docs"), glob("docs/*.md")),
        (join("share", package_name, "scripts"), glob("scripts/*.sh")),
        (join("share", package_name, "vendor"), glob(f"{package_name}/vendor/*.py")),
    ],
    install_requires=["setuptools"],
    zip_safe=True,
    maintainer="SDR Team",
    maintainer_email="sdr-team@example.com",
    description="ROS2 Python wrapper around the validated SDR receiver v67 script.",
    license="MIT",
    tests_require=["pytest"],
    entry_points={
        "console_scripts": [
            "adaptive_profile_sweep = sdr_receiver_py_wrapper.adaptive_profile_sweep:main",
            "sdr_receiver_py_wrapper_node = sdr_receiver_py_wrapper.receiver_node:main",
            "direct_original_receiver = sdr_receiver_py_wrapper.direct_original_receiver:main",
            "decoder_benchmark = sdr_receiver_py_wrapper.decoder_benchmark:main",
            "mock_radar_context_publisher = sdr_receiver_py_wrapper.mock_radar_context_publisher:main",
            "rf_iq_diff_capture = sdr_receiver_py_wrapper.rf_iq_diff_capture:main",
            "rf_power_scan = sdr_receiver_py_wrapper.rf_power_scan:main",
            "sdr_receiver_topic_monitor = sdr_receiver_py_wrapper.topic_monitor:main",
            "weak_info_probe = sdr_receiver_py_wrapper.weak_info_probe:main",
            "offline_smoke_test = sdr_receiver_py_wrapper.offline_smoke_test:main",
            "wrapper_iq_replay = sdr_receiver_py_wrapper.wrapper_iq_replay:main",
        ],
    },
)
