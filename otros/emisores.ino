#include <driver/twai.h>
// -------- CONFIGURACIÓN --------
#define MI_ID 0x124     // CAMBIAR: 0x123, 0x124, 0x125
#define NUM_NODO 2      // CAMBIAR: 1, 2, 3

// -------- PINES --------
const int PIN_SENSOR = 34;
const int PIN_RELE_VALVULA = 4;

// -------- CALIBRACIÓN --------
int secoADC   = 2460;
int humedoADC = 700;

const int N_SAMPLES = 15;

// -------- CONTROL --------
const int UMBRAL_ON_DEFAULT = 30;
const int UMBRAL_OFF_DEFAULT = 50;
int UMBRAL_ON = UMBRAL_ON_DEFAULT;
int UMBRAL_OFF = UMBRAL_OFF_DEFAULT;

bool riegoActivo = false;

// -------- VIGENCIA DE UMBRALES --------
// Equivalente funcional al watchdog del paper CIP, pero a la escala de
// tiempo de este dominio (cambios de consigna, no control en tiempo
// real): si nadie actualiza el umbral por CAN (0x202) durante este
// tiempo, se revierte a los valores de fábrica de arriba. 30 min es un
// punto de partida razonable, no un valor con significado especial --
// ajústalo según qué tan seguido el módulo de estimación (cuando
// exista) realmente publique consignas nuevas.
const unsigned long UMBRAL_TIMEOUT_MS = 1800000UL; // 30 minutos
unsigned long ultimoUmbral = 0;

// -------- MODO --------
bool modoManual = false;
uint8_t comandoManual = 0;

unsigned long ultimoComando = 0;
const unsigned long TIMEOUT_CMD = 2000;
static unsigned long ultimoEnvio = 0;
const unsigned long INTERVALO_ENVIO = 400;


// -------- CAN --------
twai_timing_config_t t_config = TWAI_TIMING_CONFIG_250KBITS();
twai_filter_config_t f_config = TWAI_FILTER_CONFIG_ACCEPT_ALL();
twai_general_config_t g_config = TWAI_GENERAL_CONFIG_DEFAULT(GPIO_NUM_21, GPIO_NUM_22, TWAI_MODE_NORMAL);

// -------- FUNCIONES --------

int leerPromedio(int pin) {
  long suma = 0;
  for (int i = 0; i < N_SAMPLES; i++) {
    suma += analogRead(pin);
    delay(5);
  }
  return suma / N_SAMPLES;
}

int calcularPorcentaje(int adc) {
  float pct = 100.0f * ((float)(adc - secoADC) / (float)(humedoADC - secoADC));
  if (pct < 0) pct = 0;
  if (pct > 100) pct = 100;
  return (int)pct;
}

// -------- SETUP --------
void setup() {

  Serial.begin(115200);

  pinMode(PIN_RELE_VALVULA, OUTPUT);
  digitalWrite(PIN_RELE_VALVULA, LOW);

  analogReadResolution(12);

  if (twai_driver_install(&g_config, &t_config, &f_config) == ESP_OK) {
    Serial.println("CAN OK");
  }

  twai_start();
}

// -------- LOOP --------
void loop() {

  // -------- RECIBIR CAN --------
  twai_message_t mensaje_rx;

  if (twai_receive(&mensaje_rx, pdMS_TO_TICKS(10)) == ESP_OK) {


    // -------- UMBRALES----------------

    if (!mensaje_rx.extd && mensaje_rx.identifier == 0x202) {

  uint8_t nodoDestino = mensaje_rx.data[0];

  if (nodoDestino == NUM_NODO) {

    UMBRAL_ON  = mensaje_rx.data[1];
    UMBRAL_OFF = mensaje_rx.data[2];
    ultimoUmbral = millis();

    Serial.printf("Nuevo UMBRAL -> ON:%d OFF:%d\n", UMBRAL_ON, UMBRAL_OFF);
  }
}

    // -------- COMANDO VALVULA --------
    if (!mensaje_rx.extd && mensaje_rx.identifier == 0x200) {

      uint8_t nodoDestino = mensaje_rx.data[0];
      uint8_t comando = mensaje_rx.data[1];

      if (nodoDestino == NUM_NODO) {
        comandoManual = comando;
        ultimoComando = millis();
        
      }
    }

    // -------- MODO --------
    if (!mensaje_rx.extd && mensaje_rx.identifier == 0x201) {

      uint8_t nodoDestino = mensaje_rx.data[0];
      uint8_t modo = mensaje_rx.data[1];

      if (nodoDestino == NUM_NODO) {
        modoManual = (modo == 1);
      }
    }
  }

  // -------- VIGENCIA DE UMBRALES --------
  if (millis() - ultimoUmbral > UMBRAL_TIMEOUT_MS) {
    if (UMBRAL_ON != UMBRAL_ON_DEFAULT || UMBRAL_OFF != UMBRAL_OFF_DEFAULT) {
      UMBRAL_ON = UMBRAL_ON_DEFAULT;
      UMBRAL_OFF = UMBRAL_OFF_DEFAULT;
      Serial.println("UMBRAL: vencio la vigencia, revertido a valores de fabrica por resguardo");
    }
  }

  // -------- LEER SENSOR --------
  int adc = leerPromedio(PIN_SENSOR);
  int humedad = calcularPorcentaje(adc);

  Serial.printf("Nodo %d -> Humedad: %d%% -> adc: %d\n", NUM_NODO, humedad, adc);


  // -------- CONTROL VALVULA --------
  uint8_t estadoValvula = 0;

  if (modoManual) {

  if (millis() - ultimoComando > TIMEOUT_CMD) {
    // Si no llegan comandos → apagar por seguridad
    estadoValvula = 0;
  } else {
    estadoValvula = comandoManual;
  }

}
else {

    // -------- HISTÉRESIS --------
    if (!riegoActivo && humedad < UMBRAL_ON) {
      riegoActivo = true;
    }

    if (riegoActivo && humedad > UMBRAL_OFF) {
      riegoActivo = false;
    }

    estadoValvula = riegoActivo;
    
  }
    digitalWrite(PIN_RELE_VALVULA, estadoValvula);

  

if (millis() - ultimoEnvio >= INTERVALO_ENVIO) {
  ultimoEnvio = millis();

  // -------- ENVIAR CAN --------
  twai_message_t msg;

  msg.identifier = MI_ID;
  msg.extd = 0;
  msg.data_length_code = 4;

  msg.data[0] = humedad;
  msg.data[1] = estadoValvula;
  msg.data[2] = UMBRAL_ON;
  msg.data[3] = UMBRAL_OFF;

  if (twai_transmit(&msg, pdMS_TO_TICKS(20)) == ESP_OK) {
    Serial.printf("Nodo %d -> Enviado CAN\n", NUM_NODO);
  }
}
}
