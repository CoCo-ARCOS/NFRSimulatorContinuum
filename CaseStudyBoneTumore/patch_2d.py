import os

def main():
    with open('workflow_dicom_by_dicom.py', 'r') as f:
        content = f.read()
        
    parts = content.split("@python_app(executors=['fog'])\ndef fog_preprocessing(input_dir, output_dir):")
    before = parts[0]
    after = parts[1]
    
    parts2 = after.split("@python_app(executors=['cloud'])\ndef cloud_inference(input_nifti, output_path):")
    after_inference = parts2[1]
    
    parts3 = after_inference.split("# ==========================================\n# Main Workflow Execution")
    
    fog_2d = """@python_app(executors=['fog'])
def fog_preprocessing(input_dir, output_dir):
    import time
    _t0 = time.time()
    import os
    import pydicom
    import nibabel as nib
    import numpy as np
    from pathlib import Path
    
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    for dcm_file in Path(input_dir).rglob('*.dcm'):
        try:
            ds = pydicom.dcmread(dcm_file)
            data = ds.pixel_array.astype(np.float32)
            # Mock isolate ROI (remove values below threshold)
            data[data < 0] = 0
            
            # Save as 2D NIfTI
            img = nib.Nifti1Image(data, np.eye(4))
            out_name = dcm_file.name.replace('.dcm', '.nii.gz')
            nib.save(img, out_path / out_name)
        except Exception as e:
            pass
            
    import time; _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'fog_preprocessing,{_t1-_t0:.4f}\\n')
    return output_dir

"""

    cloud_2d = """@python_app(executors=['cloud'])
def cloud_inference(input_dir, output_dir):
    import time
    _t0 = time.time()
    import os
    import nibabel as nib
    import numpy as np
    import torch
    import torch.nn.functional as F
    from monai.networks.nets import UNet
    from pathlib import Path
    import scipy.ndimage as ndi
    from PIL import Image
    
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    
    vis_dir = str(out_path) + "_vis"
    os.makedirs(vis_dir, exist_ok=True)
    
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    
    # Initialize a standard 2D UNet
    model = UNet(
        spatial_dims=2,
        in_channels=1,
        out_channels=1,
        channels=(16, 32, 64, 128, 256),
        strides=(2, 2, 2, 2),
        num_res_units=2,
    ).to(device)
    model.eval()
    
    for nii_file in Path(input_dir).rglob('*.nii.gz'):
        if not nii_file.is_file(): continue
        
        img = nib.load(nii_file)
        data = img.get_fdata() # Shape (H, W)
        if len(data.shape) > 2:
            data = data.squeeze()
            
        tensor_data = torch.tensor(data, dtype=torch.float32)
        while len(tensor_data.shape) < 4:
            tensor_data = tensor_data.unsqueeze(0) # [1, 1, H, W]
            
        tensor_data_resized = F.interpolate(tensor_data, size=(64, 64), mode='bilinear', align_corners=False)
        tensor_data_resized = tensor_data_resized.to(device)
        
        with torch.no_grad():
            output = model(tensor_data_resized)
            
        # Mocking dense bone isolation (2D)
        threshold_val = np.percentile(data, 99.5) if data.size > 0 else 0
        high_density = data > threshold_val
        
        labeled, num_features = ndi.label(high_density)
        if num_features > 0:
            sizes = ndi.sum(high_density, labeled, range(1, num_features + 1))
            largest_idx = np.argmax(sizes) + 1
            tumor_mask = (labeled == largest_idx)
            tumor_mask = ndi.binary_dilation(tumor_mask, iterations=3)
        else:
            tumor_mask = np.zeros_like(data, dtype=bool)
            
        final_mask = tumor_mask.astype(np.uint8)
        
        mask_img = nib.Nifti1Image(final_mask, np.eye(4))
        out_nii_path = out_path / nii_file.name
        nib.save(mask_img, out_nii_path)
        
        # Overlay visualization
        d_min, d_max = np.min(data), np.max(data)
        if d_max > d_min:
            norm_data = ((data - d_min) / (d_max - d_min) * 255).astype(np.uint8)
        else:
            norm_data = np.zeros_like(data, dtype=np.uint8)
            
        rgb_img = np.stack([norm_data, norm_data, norm_data], axis=-1)
        mask_indices = final_mask > 0
        if np.any(mask_indices):
            rgb_img[mask_indices, 0] = (0.4 * 255 + 0.6 * rgb_img[mask_indices, 0]).astype(np.uint8)
            rgb_img[mask_indices, 1] = (0.6 * rgb_img[mask_indices, 1]).astype(np.uint8)
            rgb_img[mask_indices, 2] = (0.6 * rgb_img[mask_indices, 2]).astype(np.uint8)
            
        im = Image.fromarray(rgb_img)
        vis_name = nii_file.name.replace('.nii.gz', '.png')
        im.save(os.path.join(vis_dir, vis_name))
        
    import time; _t1 = time.time()
    with open('workflow_timing.log', 'a') as _f: _f.write(f'cloud_inference,{_t1-_t0:.4f}\\n')
    return str(out_path), vis_dir

"""
    
    new_content = before + fog_2d + cloud_2d + "# ==========================================\n# Main Workflow Execution" + parts3[1]
    
    new_content = new_content.replace("cloud_output = os.path.abspath(f'{cloud_dir}/inference_mask_{i}.nii.gz')", "cloud_output = os.path.abspath(f'{cloud_dir}/inference_mask_{i}')")
    
    with open('workflow_dicom_by_dicom.py', 'w') as f:
        f.write(new_content)

if __name__ == "__main__":
    main()
