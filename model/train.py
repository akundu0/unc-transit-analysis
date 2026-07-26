"""
Offline batch training script.

Usage:
    python -m model.train

Trains a small feedforward Keras model on accumulated historical data and
saves the artifact to model/artifacts/delay_model.keras.

Design notes:
- Train/eval split is time-ordered (not random) because this is time-series
  data; shuffling would leak future information into the training set.
- The preprocessing StandardScaler is saved alongside the model so the serving
  layer applies the same normalisation.
"""

import logging
import os
import pickle
from pathlib import Path

import numpy as np
import tensorflow as tf
from sklearn.preprocessing import StandardScaler

from features.engineering import build_training_dataset, FEATURE_NAMES

log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)

ARTIFACTS_DIR = Path(__file__).parent / "artifacts"
MODEL_PATH = ARTIFACTS_DIR / "delay_model.keras"
SCALER_PATH = ARTIFACTS_DIR / "scaler.pkl"

TRAIN_FRACTION = 0.8       # chronological split point
EPOCHS = 30
BATCH_SIZE = 32
EARLY_STOP_PATIENCE = 5


def build_model(n_features: int) -> tf.keras.Model:
    """Small feedforward network suitable for tabular time-series prediction."""
    inputs = tf.keras.Input(shape=(n_features,), name="features")
    x = tf.keras.layers.Dense(64, activation="relu")(inputs)
    x = tf.keras.layers.Dropout(0.2)(x)
    x = tf.keras.layers.Dense(32, activation="relu")(x)
    x = tf.keras.layers.Dropout(0.1)(x)
    output = tf.keras.layers.Dense(1, name="delay_seconds")(x)

    model = tf.keras.Model(inputs, output)
    model.compile(
        optimizer=tf.keras.optimizers.Adam(learning_rate=1e-3),
        loss="mse",
        metrics=["mae"],
    )
    return model


def train() -> None:
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

    log.info("Loading training dataset from DB…")
    df = build_training_dataset()

    if df.empty or len(df) < 10:
        log.error(
            "Not enough training data (%d rows). Run the ingestion poller "
            "to accumulate historical data first.", len(df)
        )
        return

    # Chronological split (no shuffle — time-series integrity)
    split_idx = int(len(df) * TRAIN_FRACTION)
    train_df = df.iloc[:split_idx]
    eval_df = df.iloc[split_idx:]

    log.info("Train rows: %d  |  Eval rows: %d", len(train_df), len(eval_df))

    X_train = train_df[FEATURE_NAMES].values.astype(np.float32)
    y_train = train_df["label"].values.astype(np.float32)
    X_eval = eval_df[FEATURE_NAMES].values.astype(np.float32)
    y_eval = eval_df["label"].values.astype(np.float32)

    # Fit scaler on TRAIN only — must not see eval data
    scaler = StandardScaler()
    X_train = scaler.fit_transform(X_train)
    X_eval = scaler.transform(X_eval)

    log.info("Saving scaler → %s", SCALER_PATH)
    with open(SCALER_PATH, "wb") as f:
        pickle.dump(scaler, f)

    model = build_model(n_features=len(FEATURE_NAMES))
    model.summary(print_fn=log.info)

    callbacks = [
        tf.keras.callbacks.EarlyStopping(
            monitor="val_loss",
            patience=EARLY_STOP_PATIENCE,
            restore_best_weights=True,
        ),
        tf.keras.callbacks.ModelCheckpoint(
            filepath=str(MODEL_PATH),
            save_best_only=True,
            monitor="val_loss",
        ),
    ]

    history = model.fit(
        X_train, y_train,
        validation_data=(X_eval, y_eval),
        epochs=EPOCHS,
        batch_size=BATCH_SIZE,
        callbacks=callbacks,
        verbose=1,
    )

    final_val_mae = min(history.history["val_mae"])
    log.info("Training complete. Best val MAE: %.1f seconds", final_val_mae)
    log.info("Model saved → %s", MODEL_PATH)


if __name__ == "__main__":
    train()
