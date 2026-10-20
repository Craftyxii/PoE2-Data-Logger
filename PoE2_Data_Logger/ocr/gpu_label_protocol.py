"""Pure constants shared with the separately packaged, optional GPU label helper."""

PROTOCOL_VERSION = 1
MODEL_SHA256 = "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884"
LABEL_SHAPE = (3, 48, 320)
MODEL_CLASSES = 18710
MAX_INPUT_BYTES = 128 * 1024 * 1024
MAX_ROWS = 1024
MIN_BATCH_SIZE = 1
MAX_BATCH_SIZE = 64
DML_PROVIDER = "DmlExecutionProvider"
CPU_PROVIDER = "CPUExecutionProvider"
