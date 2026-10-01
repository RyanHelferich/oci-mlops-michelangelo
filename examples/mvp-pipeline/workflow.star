load("@plugin", "storage", "workflow")

# Michelangelo's registered starlark-workflow executes this packaged UniFlow.
# storage.read schedules a real worker activity against the configured OCI bucket.
def train(dataset_url):
    data = storage.read(url = dataset_url)
    x = data["x"]
    y = data["y"]
    n = len(x)
    x_total = 0.0
    y_total = 0.0
    for i in range(n):
        x_total += x[i]
        y_total += y[i]
    x_mean = x_total / n
    y_mean = y_total / n
    numerator = 0.0
    denominator = 0.0
    for i in range(n):
        numerator += (x[i] - x_mean) * (y[i] - y_mean)
        dx = x[i] - x_mean
        denominator += dx * dx
    slope = numerator / denominator
    intercept = y_mean - slope * x_mean
    predictions = [intercept + slope * value for value in x]
    squared_error = 0.0
    for i in range(n):
        error = predictions[i] - y[i]
        squared_error += error * error
    mse = squared_error / n
    return {
        "algorithm": "ordinary_least_squares",
        "coefficients": {"intercept": intercept, "slope": slope},
        "metrics": {"mse": mse, "rows": n},
        "predictions": predictions,
        "dataset_url": dataset_url,
        "workflow_id": workflow.execution_id,
        "workflow_run_id": workflow.execution_run_id,
    }
