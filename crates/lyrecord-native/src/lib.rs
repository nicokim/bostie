use lyrecord_core::{Core, CoreError, HttpRequest, Message, RawBellHandle, Ticket, describe};
use pyo3::IntoPyObjectExt;
use pyo3::exceptions::{PyOSError, PyRuntimeError, PyValueError};
use pyo3::intern;
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyList};

#[global_allocator]
static ALLOCATOR: mimalloc::MiMalloc = mimalloc::MiMalloc;

#[pyclass(frozen, name = "Core", module = "lyrecord._native")]
struct NativeCore(Core);

#[pymethods]
impl NativeCore {
    #[staticmethod]
    fn create() -> PyResult<(Self, RawBellHandle)> {
        let (core, bell) = Core::new().map_err(to_python_error)?;
        Ok((Self(core), bell.into_raw()))
    }

    #[pyo3(signature = (method, url, headers, body=None))]
    fn submit_http(
        &self,
        method: &str,
        url: &str,
        headers: Vec<(String, String)>,
        body: Option<Vec<u8>>,
    ) -> PyResult<u64> {
        let request = HttpRequest::parse(method, url, &headers, body).map_err(to_python_error)?;
        let ticket = self.0.submit_http(request).map_err(to_python_error)?;
        Ok(ticket.into())
    }

    fn cancel(&self, ticket: u64) -> PyResult<()> {
        self.0.cancel(Ticket::from(ticket)).map_err(to_python_error)
    }

    fn drain<'py>(&self, py: Python<'py>) -> PyResult<Bound<'py, PyList>> {
        let messages = self.0.drain().map_err(to_python_error)?;
        let items = messages
            .into_iter()
            .map(|message| to_python_message(py, message))
            .collect::<PyResult<Vec<_>>>()?;
        PyList::new(py, items)
    }

    fn inflight(&self) -> PyResult<usize> {
        self.0.inflight().map_err(to_python_error)
    }

    fn close(&self) -> PyResult<()> {
        self.0.close().map_err(to_python_error)
    }
}

fn to_python_message(py: Python<'_>, message: Message) -> PyResult<Bound<'_, PyAny>> {
    match message {
        Message::Response { ticket, response } => (
            intern!(py, "response"),
            u64::from(ticket),
            response.status,
            response.headers,
            PyBytes::new(py, &response.body),
        )
            .into_bound_py_any(py),
        Message::Failure { ticket, detail } => {
            (intern!(py, "failure"), u64::from(ticket), detail).into_bound_py_any(py)
        }
    }
}

fn to_python_error(error: CoreError) -> PyErr {
    let message = describe(&error);
    match error {
        CoreError::InvalidRequest(_) => PyValueError::new_err(message),
        CoreError::Forked { .. } | CoreError::Closed => PyRuntimeError::new_err(message),
        CoreError::Runtime(_) | CoreError::Bell(_) | CoreError::HttpClient(_) => {
            PyOSError::new_err(message)
        }
    }
}

#[pymodule]
mod _native {
    #[pymodule_export]
    use super::NativeCore;
}
