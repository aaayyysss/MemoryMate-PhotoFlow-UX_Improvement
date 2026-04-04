"""
Library Detector Service
Professional dependency checking system following Google Photos and Lightroom patterns.

Checks for:
- ML libraries (PyTorch, Transformers)
- CLIP models availability
- Hardware acceleration (CUDA/MPS)
- System resources
"""

import os
import sys
import platform
from pathlib import Path
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass

from logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class LibraryStatus:
    """Status information for a library or dependency."""
    name: str
    available: bool
    version: Optional[str]
    message: str
    recommendations: List[str]


@dataclass
class SystemInfo:
    """System hardware and environment information."""
    os_name: str
    os_version: str
    python_version: str
    cpu_count: int
    ram_gb: float
    gpu_available: bool
    cuda_available: bool
    mps_available: bool


class LibraryDetector:
    """
    Professional library detection service.
    
    Follows best practices from:
    - Google Photos: Comprehensive system checks
    - Lightroom: Clear status reporting
    - iPhone Photos: User-friendly recommendations
    """

    def __init__(self):
        self._cache = {}

    def get_system_info(self) -> SystemInfo:
        """Get comprehensive system information."""
        try:
            # OS Information
            os_name = platform.system()
            os_version = platform.version()
            
            # Python
            python_version = sys.version.split()[0]
            
            # CPU
            cpu_count = os.cpu_count() or 1
            
            # RAM (approximate)
            try:
                if os_name == "Windows":
                    import psutil
                    ram_gb = psutil.virtual_memory().total / (1024**3)
                else:
                    ram_gb = 8.0  # Default assumption
            except:
                ram_gb = 8.0
            
            # GPU Detection
            gpu_available = False
            cuda_available = False
            mps_available = False
            
            try:
                import torch
                if torch.cuda.is_available():
                    cuda_available = True
                    gpu_available = True
                
                if hasattr(torch.backends, 'mps') and torch.backends.mps.is_available():
                    mps_available = True
                    gpu_available = True
                    
            except ImportError:
                pass
            
            return SystemInfo(
                os_name=os_name,
                os_version=os_version,
                python_version=python_version,
                cpu_count=cpu_count,
                ram_gb=round(ram_gb, 1),
                gpu_available=gpu_available,
                cuda_available=cuda_available,
                mps_available=mps_available
            )
            
        except Exception as e:
            logger.error(f"Failed to get system info: {e}")
            return SystemInfo(
                os_name="Unknown",
                os_version="Unknown",
                python_version="Unknown",
                cpu_count=1,
                ram_gb=0.0,
                gpu_available=False,
                cuda_available=False,
                mps_available=False
            )

    def check_pytorch(self) -> LibraryStatus:
        """Check PyTorch availability and version."""
        try:
            import torch
            version = torch.__version__
            
            recommendations = []
            if not torch.cuda.is_available():
                recommendations.append("Install CUDA toolkit for GPU acceleration")
            
            return LibraryStatus(
                name="PyTorch",
                available=True,
                version=version,
                message=f"PyTorch {version} installed",
                recommendations=recommendations
            )
            
        except ImportError:
            return LibraryStatus(
                name="PyTorch",
                available=False,
                version=None,
                message="PyTorch not installed - Required for AI features",
                recommendations=[
                    "pip install torch torchvision torchaudio",
                    "Visit https://pytorch.org/get-started/locally/"
                ]
            )

    def check_transformers(self) -> LibraryStatus:
        """Check Hugging Face Transformers availability."""
        try:
            import transformers
            version = transformers.__version__
            
            return LibraryStatus(
                name="Transformers",
                available=True,
                version=version,
                message=f"Hugging Face Transformers {version} installed",
                recommendations=[]
            )
            
        except ImportError:
            return LibraryStatus(
                name="Transformers",
                available=False,
                version=None,
                message="Transformers not installed - Required for CLIP models",
                recommendations=[
                    "pip install transformers",
                    "Also installs tokenizers and huggingface-hub"
                ]
            )

    def check_clip_models(self) -> LibraryStatus:
        """Check CLIP model availability."""
        try:
            from utils.clip_check import check_clip_availability, get_recommended_variant
            
            variant = get_recommended_variant()
            available, message = check_clip_availability(variant)
            
            if available:
                return LibraryStatus(
                    name="CLIP Models",
                    available=True,
                    version=variant,
                    message=message,
                    recommendations=[]
                )
            else:
                return LibraryStatus(
                    name="CLIP Models",
                    available=False,
                    version=None,
                    message=message,
                    recommendations=[
                        f"python download_clip_model_offline.py --variant {variant}",
                        "Models will be downloaded to ./models/ directory",
                        "Requires ~3GB disk space"
                    ]
                )
                
        except Exception as e:
            return LibraryStatus(
                name="CLIP Models",
                available=False,
                version=None,
                message=f"Error checking CLIP models: {e}",
                recommendations=[
                    "Check utils.clip_check module",
                    "Ensure models directory exists"
                ]
            )

    def check_opencv(self) -> LibraryStatus:
        """Check OpenCV availability."""
        try:
            import cv2
            version = cv2.__version__
            
            return LibraryStatus(
                name="OpenCV",
                available=True,
                version=version,
                message=f"OpenCV {version} installed",
                recommendations=[]
            )
            
        except ImportError:
            return LibraryStatus(
                name="OpenCV",
                available=False,
                version=None,
                message="OpenCV not installed - Used for image processing",
                recommendations=[
                    "pip install opencv-python",
                    "Required for advanced image analysis"
                ]
            )

    def check_sklearn(self) -> LibraryStatus:
        """Check scikit-learn availability."""
        try:
            import sklearn
            version = sklearn.__version__
            
            return LibraryStatus(
                name="scikit-learn",
                available=True,
                version=version,
                message=f"scikit-learn {version} installed",
                recommendations=[]
            )
            
        except ImportError:
            return LibraryStatus(
                name="scikit-learn",
                available=False,
                version=None,
                message="scikit-learn not installed - Used for clustering algorithms",
                recommendations=[
                    "pip install scikit-learn",
                    "Required for similarity detection"
                ]
            )

    def get_all_status(self) -> Dict[str, LibraryStatus]:
        """Get status for all critical libraries."""
        status_dict = {}
        
        # Core ML Libraries
        status_dict['pytorch'] = self.check_pytorch()
        status_dict['transformers'] = self.check_transformers()
        status_dict['clip_models'] = self.check_clip_models()
        
        # Supporting Libraries
        status_dict['opencv'] = self.check_opencv()
        status_dict['sklearn'] = self.check_sklearn()
        
        return status_dict

    def get_readiness_summary(self) -> Tuple[bool, str, List[str]]:
        """
        Get overall readiness assessment.
        
        Returns:
            Tuple of (ready, summary_message, recommendations)
        """
        statuses = self.get_all_status()
        
        # Critical libraries for duplicate detection
        critical_libs = ['pytorch', 'transformers', 'clip_models']
        critical_ready = all(statuses[lib].available for lib in critical_libs)
        
        # Count available libraries
        available_count = sum(1 for status in statuses.values() if status.available)
        total_count = len(statuses)
        
        if critical_ready:
            summary = f"✅ Ready for AI features ({available_count}/{total_count} libraries)"
            recommendations = []
        else:
            missing = [name for name, status in statuses.items() 
                      if name in critical_libs and not status.available]
            summary = f"⚠️ Missing critical libraries: {', '.join(missing)}"
            
            # Collect recommendations
            recommendations = []
            for lib_name in missing:
                recommendations.extend(statuses[lib_name].recommendations)
        
        return critical_ready, summary, list(set(recommendations))

    def generate_system_report(self) -> str:
        """Generate comprehensive system report."""
        system_info = self.get_system_info()
        statuses = self.get_all_status()
        ready, summary, recommendations = self.get_readiness_summary()
        
        report_lines = []
        report_lines.append("=" * 60)
        report_lines.append("SYSTEM & LIBRARY DETECTION REPORT")
        report_lines.append("=" * 60)
        report_lines.append("")
        
        # System Information
        report_lines.append("🖥️  SYSTEM INFORMATION")
        report_lines.append("-" * 30)
        report_lines.append(f"OS: {system_info.os_name} {system_info.os_version}")
        report_lines.append(f"Python: {system_info.python_version}")
        report_lines.append(f"CPU Cores: {system_info.cpu_count}")
        report_lines.append(f"RAM: {system_info.ram_gb} GB")
        report_lines.append(f"GPU Available: {'Yes' if system_info.gpu_available else 'No'}")
        if system_info.gpu_available:
            if system_info.cuda_available:
                report_lines.append("  - CUDA: Available")
            if system_info.mps_available:
                report_lines.append("  - MPS (Apple Metal): Available")
        report_lines.append("")
        
        # Library Status
        report_lines.append("📚 LIBRARY STATUS")
        report_lines.append("-" * 30)
        for lib_name, status in statuses.items():
            icon = "✅" if status.available else "❌"
            report_lines.append(f"{icon} {status.name}: {status.message}")
            if status.version and status.available:
                report_lines.append(f"    Version: {status.version}")
        report_lines.append("")
        
        # Readiness Summary
        report_lines.append("📊 READINESS ASSESSMENT")
        report_lines.append("-" * 30)
        report_lines.append(summary)
        report_lines.append("")
        
        # Recommendations
        if recommendations:
            report_lines.append("💡 RECOMMENDATIONS")
            report_lines.append("-" * 30)
            for rec in recommendations:
                report_lines.append(f"• {rec}")
            report_lines.append("")
        
        # Best Practices Notes
        report_lines.append("📋 BEST PRACTICES")
        report_lines.append("-" * 30)
        report_lines.append("• Google Photos: Runs duplicate detection automatically during indexing")
        report_lines.append("• Lightroom: Checks for GPU acceleration before processing")
        report_lines.append("• iPhone Photos: Downloads models on-demand via App Store")
        report_lines.append("• Recommended: Keep CLIP models updated for better accuracy")
        report_lines.append("")
        
        return "\n".join(report_lines)


# Global detector instance
detector = LibraryDetector()


def check_system_readiness() -> Tuple[bool, str, List[str]]:
    """
    Quick check for system readiness.
    
    Returns:
        (ready: bool, summary: str, recommendations: List[str])
    """
    return detector.get_readiness_summary()


def print_system_report():
    """Print comprehensive system report to console."""
    print(detector.generate_system_report())


if __name__ == "__main__":
    print_system_report()
