const fileInput = document.querySelector("#file");
const selectedFileName = document.querySelector("#selected-file-name");
const uploadForm = document.querySelector(".upload-form");
const progressContainer = document.querySelector("#upload-progress");
const progressBar = document.querySelector("#upload-progress-bar");
const progressPercent = document.querySelector("#upload-percent");
const uploadStatus = document.querySelector("#upload-status");

if (fileInput && selectedFileName) {
  fileInput.addEventListener("change", () => {
    const files = Array.from(fileInput.files ?? []);
    selectedFileName.textContent = files.length
      ? `${files.length} file(s) selected: ${files.map((file) => file.name).join(", ")}`
      : "No file selected";
    selectedFileName.classList.toggle("has-file", files.length > 0);
  });
}

if (
  fileInput instanceof HTMLInputElement &&
  uploadForm instanceof HTMLFormElement &&
  progressContainer instanceof HTMLElement &&
  progressBar instanceof HTMLProgressElement &&
  progressPercent instanceof HTMLElement &&
  uploadStatus instanceof HTMLElement
) {
  uploadForm.addEventListener("submit", (event) => {
    const files = Array.from(fileInput.files ?? []);
    if (files.length === 0) {
      return;
    }

    const maxFiles = Number(fileInput.dataset.maxFiles);
    let validationMessage = "";
    if (files.length > maxFiles) {
      validationMessage = `You can upload no more than ${maxFiles} file(s) at a time.`;
    }
    if (validationMessage) {
      event.preventDefault();
      progressContainer.hidden = false;
      uploadStatus.textContent = validationMessage;
      progressPercent.textContent = "";
      return;
    }

    event.preventDefault();
    const formData = new FormData(uploadForm);
    const xhr = new XMLHttpRequest();
    const submitButton = uploadForm.querySelector('button[type="submit"]');
    progressContainer.hidden = false;
    progressBar.value = 0;
    progressPercent.textContent = "0%";
    uploadStatus.textContent = `Uploading ${files.length} file(s)…`;
    if (submitButton instanceof HTMLButtonElement) {
      submitButton.disabled = true;
    }

    xhr.upload.addEventListener("progress", (progressEvent) => {
      if (!progressEvent.lengthComputable) {
        return;
      }
      const percent = Math.round((progressEvent.loaded / progressEvent.total) * 100);
      progressBar.value = percent;
      progressPercent.textContent = `${percent}%`;
    });

    xhr.addEventListener("load", () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        progressBar.value = 100;
        progressPercent.textContent = "100%";
        uploadStatus.textContent = "Upload complete.";
        window.location.reload();
        return;
      }

      let message = "Upload failed. Please try again.";
      try {
        const response = JSON.parse(xhr.responseText);
        if (typeof response.error === "string") {
          message = response.error;
        }
      } catch {
        if (xhr.status === 413) {
          message = "The upload request exceeds the allowed size.";
        }
      }
      uploadStatus.textContent = message;
      if (submitButton instanceof HTMLButtonElement) {
        submitButton.disabled = false;
      }
    });

    xhr.addEventListener("error", () => {
      uploadStatus.textContent = "Network error. Check your connection and try again.";
      if (submitButton instanceof HTMLButtonElement) {
        submitButton.disabled = false;
      }
    });

    xhr.open("POST", uploadForm.action);
    xhr.setRequestHeader("X-Requested-With", "XMLHttpRequest");
    xhr.send(formData);
  });
}
